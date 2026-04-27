import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from html import escape

from aiogram import Bot, Dispatcher
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from app.bot import create_bot, create_dispatcher
from app.config import settings
from app.database import SessionLocal, get_session, init_db
from app.reports import export_excel, export_pdf
from app.services import (
    authenticate_admin,
    broadcast_stats,
    dashboard_stats,
    delivery_rows,
    distinct_values,
    get_broadcast,
    list_broadcasts,
    list_employees,
    list_questions,
    resolve_recipients,
    response_label,
    save_hr_answer,
    seed_admin,
    sync_superuser_role,
    send_broadcast,
    toggle_employee,
    toggle_employee_admin,
    create_broadcast,
)

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["response_label"] = response_label
templates.env.globals["now"] = datetime.utcnow


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as session:
        await seed_admin(session)
        await sync_superuser_role(session)

    bot: Bot | None = None
    dispatcher: Dispatcher | None = None
    polling_task: asyncio.Task | None = None
    if settings.bot_token:
        bot = create_bot(settings.bot_token)
        dispatcher = create_dispatcher()
        polling_task = asyncio.create_task(dispatcher.start_polling(bot))
    app.state.bot = bot

    try:
        yield
    finally:
        if polling_task:
            polling_task.cancel()
            with suppress(asyncio.CancelledError):
                await polling_task
        if bot:
            await bot.session.close()


app = FastAPI(title="HR Telegram Bot", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def require_admin(request: Request) -> str:
    username = request.session.get("admin")
    if not username:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return username


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, session: AsyncSession = Depends(get_session), admin: str = Depends(require_admin)):
    stats = await dashboard_stats(session)
    broadcasts = (await list_broadcasts(session))[:5]
    questions = (await list_questions(session))[:5]
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "admin": admin, "stats": stats, "broadcasts": broadcasts, "questions": questions},
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    user = await authenticate_admin(session, username, password)
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Логин немесе пароль қате"}, status_code=401)
    request.session["admin"] = user.username
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/employees", response_class=HTMLResponse)
async def employees_page(
    request: Request,
    department: str | None = None,
    position: str | None = None,
    search: str | None = None,
    session: AsyncSession = Depends(get_session),
    admin: str = Depends(require_admin),
):
    employees = await list_employees(session, department=department, position=position, search=search)
    departments = await distinct_values(session, "department")
    positions = await distinct_values(session, "position")
    return templates.TemplateResponse(
        "employees.html",
        {
            "request": request,
            "admin": admin,
            "employees": employees,
            "departments": departments,
            "positions": positions,
            "filters": {"department": department or "", "position": position or "", "search": search or ""},
        },
    )


@app.post("/employees/{employee_id}/toggle")
async def employee_toggle(employee_id: int, session: AsyncSession = Depends(get_session), admin: str = Depends(require_admin)):
    await toggle_employee(session, employee_id)
    return RedirectResponse("/employees", status_code=303)


@app.post("/employees/{employee_id}/toggle-admin")
async def employee_toggle_admin(employee_id: int, session: AsyncSession = Depends(get_session), admin: str = Depends(require_admin)):
    await toggle_employee_admin(session, employee_id)
    return RedirectResponse("/employees", status_code=303)


@app.get("/broadcasts", response_class=HTMLResponse)
async def broadcasts_page(request: Request, session: AsyncSession = Depends(get_session), admin: str = Depends(require_admin)):
    broadcasts = await list_broadcasts(session)
    return templates.TemplateResponse("broadcasts.html", {"request": request, "admin": admin, "broadcasts": broadcasts})


@app.get("/broadcasts/new", response_class=HTMLResponse)
async def new_broadcast_page(request: Request, session: AsyncSession = Depends(get_session), admin: str = Depends(require_admin)):
    employees = await list_employees(session, active_only=True)
    departments = await distinct_values(session, "department")
    positions = await distinct_values(session, "position")
    return templates.TemplateResponse(
        "broadcast_new.html",
        {
            "request": request,
            "admin": admin,
            "employees": employees,
            "departments": departments,
            "positions": positions,
            "bot_enabled": bool(settings.bot_token),
        },
    )


@app.post("/broadcasts")
async def create_broadcast_route(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: str = Depends(require_admin),
):
    form = await request.form()
    title = str(form.get("title", "")).strip()
    text = str(form.get("text", "")).strip()
    target_type = str(form.get("target_type", "all"))
    department = str(form.get("department", "")).strip() or None
    position = str(form.get("position", "")).strip() or None
    selected_ids = [int(value) for value in form.getlist("employee_ids") if str(value).isdigit()]
    if not title or not text:
        raise HTTPException(status_code=400, detail="Title and text are required")

    target_value = None
    if target_type == "department":
        target_value = department
    elif target_type == "position":
        target_value = position
    elif target_type == "selected":
        target_value = ",".join(str(item) for item in selected_ids)

    recipients = await resolve_recipients(session, target_type, department, position, selected_ids)
    broadcast = await create_broadcast(session, title, text, target_type, recipients, target_value)

    bot = getattr(request.app.state, "bot", None)
    if bot and recipients:
        await send_broadcast(session, bot, broadcast.id)
    return RedirectResponse(f"/broadcasts/{broadcast.id}", status_code=303)


@app.post("/broadcasts/{broadcast_id}/send")
async def resend_broadcast(request: Request, broadcast_id: int, session: AsyncSession = Depends(get_session), admin: str = Depends(require_admin)):
    bot = getattr(request.app.state, "bot", None)
    if bot:
        await send_broadcast(session, bot, broadcast_id)
    return RedirectResponse(f"/broadcasts/{broadcast_id}", status_code=303)


@app.get("/broadcasts/{broadcast_id}", response_class=HTMLResponse)
async def broadcast_detail(request: Request, broadcast_id: int, session: AsyncSession = Depends(get_session), admin: str = Depends(require_admin)):
    broadcast = await get_broadcast(session, broadcast_id)
    if not broadcast:
        raise HTTPException(status_code=404)
    deliveries = await delivery_rows(session, broadcast_id)
    stats = broadcast_stats(deliveries)
    return templates.TemplateResponse(
        "broadcast_detail.html",
        {
            "request": request,
            "admin": admin,
            "broadcast": broadcast,
            "deliveries": deliveries,
            "stats": stats,
            "bot_enabled": bool(settings.bot_token),
        },
    )


@app.get("/broadcasts/{broadcast_id}/export.xlsx")
async def export_broadcast_xlsx(broadcast_id: int, session: AsyncSession = Depends(get_session), admin: str = Depends(require_admin)):
    deliveries = await delivery_rows(session, broadcast_id)
    content = export_excel(deliveries)
    headers = {"Content-Disposition": f'attachment; filename="broadcast-{broadcast_id}.xlsx"'}
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)


@app.get("/broadcasts/{broadcast_id}/export.pdf")
async def export_broadcast_pdf(broadcast_id: int, session: AsyncSession = Depends(get_session), admin: str = Depends(require_admin)):
    broadcast = await get_broadcast(session, broadcast_id)
    if not broadcast:
        raise HTTPException(status_code=404)
    deliveries = await delivery_rows(session, broadcast_id)
    content = export_pdf(deliveries, broadcast.title)
    headers = {"Content-Disposition": f'attachment; filename="broadcast-{broadcast_id}.pdf"'}
    return Response(content, media_type="application/pdf", headers=headers)


@app.get("/questions", response_class=HTMLResponse)
async def questions_page(request: Request, session: AsyncSession = Depends(get_session), admin: str = Depends(require_admin)):
    questions = await list_questions(session)
    return templates.TemplateResponse(
        "questions.html",
        {"request": request, "admin": admin, "questions": questions, "bot_enabled": bool(settings.bot_token)},
    )


@app.post("/questions/{delivery_id}/answer")
async def answer_question(
    request: Request,
    delivery_id: int,
    answer: str = Form(...),
    session: AsyncSession = Depends(get_session),
    admin: str = Depends(require_admin),
):
    delivery = await save_hr_answer(session, delivery_id, answer.strip())
    bot = getattr(request.app.state, "bot", None)
    if bot and delivery:
        await bot.send_message(
            delivery.employee.telegram_id,
            f"<b>HR жауабы</b>\n\n{escape(answer.strip())}\n\nХабарлама: {escape(delivery.broadcast.title)}",
        )
    return RedirectResponse("/questions", status_code=303)


@app.get("/health")
async def health():
    return {"status": "ok", "bot": bool(settings.bot_token)}

import asyncio
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from app.database import SessionLocal
from app.models import ResponseType
from app.services import (
    broadcast_stats,
    create_broadcast,
    dashboard_stats,
    delivery_rows,
    distinct_values,
    get_admin_employee,
    get_employee_by_telegram_id,
    get_superuser_employee,
    list_employees,
    list_questions,
    register_employee,
    resolve_recipients,
    save_hr_answer,
    save_question,
    send_broadcast,
    toggle_employee_admin,
    set_delivery_response,
)


class Registration(StatesGroup):
    full_name = State()
    department = State()
    position = State()
    phone = State()
    employee_no = State()


class Feedback(StatesGroup):
    question = State()


class AdminBroadcast(StatesGroup):
    title = State()
    text = State()
    department = State()
    position = State()
    selected = State()


class AdminAnswer(StatesGroup):
    answer = State()


router = Router()

DEFAULT_CLEAR_LIMIT = 100
MAX_CLEAR_LIMIT = 500


def create_bot(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


def admin_menu_keyboard(is_superuser: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="Статистика", callback_data="admin:stats"),
            InlineKeyboardButton(text="Сотрудники", callback_data="admin:employees"),
        ],
        [
            InlineKeyboardButton(text="Рассылка", callback_data="admin:broadcast"),
            InlineKeyboardButton(text="Вопросы", callback_data="admin:questions"),
        ],
    ]
    if is_superuser:
        rows.append([InlineKeyboardButton(text="Роли админов", callback_data="admin:roles")])
    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def target_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Всем активным", callback_data="admin:btarget:all")],
            [InlineKeyboardButton(text="По отделу", callback_data="admin:btarget:department")],
            [InlineKeyboardButton(text="По должности", callback_data="admin:btarget:position")],
            [InlineKeyboardButton(text="Выбранным ID", callback_data="admin:btarget:selected")],
            [InlineKeyboardButton(text="Отмена", callback_data="admin:menu")],
        ]
    )


def back_to_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Назад в админку", callback_data="admin:menu")]]
    )


async def is_admin(telegram_id: int) -> bool:
    async with SessionLocal() as session:
        return await get_admin_employee(session, telegram_id) is not None


async def current_admin(telegram_id: int):
    async with SessionLocal() as session:
        return await get_admin_employee(session, telegram_id)


async def is_superuser(telegram_id: int) -> bool:
    async with SessionLocal() as session:
        return await get_superuser_employee(session, telegram_id) is not None


async def deny_callback(callback: CallbackQuery) -> bool:
    if await is_admin(callback.from_user.id):
        return False
    await callback.answer("Нет доступа. Админскую роль нужно выдать в веб-панели.", show_alert=True)
    return True


async def deny_superuser_callback(callback: CallbackQuery) -> bool:
    if await is_superuser(callback.from_user.id):
        return False
    await callback.answer("Это действие доступно только суперпользователю.", show_alert=True)
    return True


async def deny_message(message: Message) -> bool:
    if await is_admin(message.from_user.id):
        return False
    await message.answer("Нет доступа. Сначала HR должен выдать вам роль админа в веб-панели.")
    return True


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    async with SessionLocal() as session:
        employee = await get_employee_by_telegram_id(session, message.from_user.id)
    if employee and employee.is_active:
        admin_hint = "\n\nУ вас есть админская роль. Откройте панель командой /admin." if employee.is_admin else ""
        await message.answer(
            f"Сәлеметсіз бе, {employee.full_name}! Вы уже зарегистрированы.{admin_hint}"
        )
        return
    if employee and not employee.is_active:
        await message.answer("Ваш аккаунт временно отключен. Обратитесь в HR.")
        return
    await state.set_state(Registration.full_name)
    await message.answer("Сәлеметсіз бе! Для регистрации напишите ваше ФИО полностью.")


@router.message(Registration.full_name)
async def reg_full_name(message: Message, state: FSMContext) -> None:
    await state.update_data(full_name=message.text.strip())
    await state.set_state(Registration.department)
    await message.answer("Напишите ваш отдел. Например: IT, HR, Бухгалтерия.")


@router.message(Registration.department)
async def reg_department(message: Message, state: FSMContext) -> None:
    await state.update_data(department=message.text.strip())
    await state.set_state(Registration.position)
    await message.answer("Напишите вашу должность.")


@router.message(Registration.position)
async def reg_position(message: Message, state: FSMContext) -> None:
    await state.update_data(position=message.text.strip())
    await state.set_state(Registration.phone)
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отправить телефон", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer("Напишите телефон или отправьте его кнопкой.", reply_markup=keyboard)


@router.message(Registration.phone)
async def reg_phone(message: Message, state: FSMContext) -> None:
    phone = message.contact.phone_number if message.contact else (message.text or "").strip()
    await state.update_data(phone=phone)
    await state.set_state(Registration.employee_no)
    await message.answer("Напишите табельный номер. Если его нет, отправьте '-'.", reply_markup=ReplyKeyboardRemove())


@router.message(Registration.employee_no)
async def reg_employee_no(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    employee_no = (message.text or "").strip()
    if employee_no == "-":
        employee_no = None
    async with SessionLocal() as session:
        employee = await register_employee(
            session=session,
            telegram_id=message.from_user.id,
            full_name=data["full_name"],
            department=data["department"],
            position=data["position"],
            phone=data["phone"],
            employee_no=employee_no,
        )
    await state.clear()
    await message.answer(f"Регистрация завершена, {employee.full_name}. Теперь вы будете получать HR-сообщения.")


@router.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    admin = await current_admin(message.from_user.id)
    if not admin:
        await message.answer("Нет доступа. Сначала HR должен выдать вам роль админа в веб-панели.")
        return
    title = "Суперюзер-панель Telegram" if admin.is_superuser else "Админ-панель Telegram"
    await message.answer(title, reply_markup=admin_menu_keyboard(admin.is_superuser))


@router.message(Command("clear"))
async def clear_chat(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    args = (message.text or "").split(maxsplit=1)
    limit = DEFAULT_CLEAR_LIMIT
    if len(args) > 1 and args[1].strip().isdigit():
        limit = int(args[1].strip())
    limit = max(1, min(limit, MAX_CLEAR_LIMIT))

    deleted = 0
    start_message_id = message.message_id
    for message_id in range(start_message_id, max(0, start_message_id - limit), -1):
        try:
            await bot.delete_message(message.chat.id, message_id)
            deleted += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
        except (TelegramBadRequest, TelegramForbiddenError):
            continue
        if deleted and deleted % 25 == 0:
            await asyncio.sleep(0.2)

    notice = await message.answer(f"Удалено сообщений: {deleted}.")
    await asyncio.sleep(3)
    try:
        await bot.delete_message(message.chat.id, notice.message_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        pass


@router.callback_query(F.data == "admin:menu")
async def admin_menu(callback: CallbackQuery, state: FSMContext) -> None:
    admin = await current_admin(callback.from_user.id)
    if not admin:
        await callback.answer("Нет доступа. Админскую роль нужно выдать в веб-панели.", show_alert=True)
        return
    await state.clear()
    title = "Суперюзер-панель Telegram" if admin.is_superuser else "Админ-панель Telegram"
    await callback.message.answer(title, reply_markup=admin_menu_keyboard(admin.is_superuser))
    await callback.answer()


@router.callback_query(F.data == "admin:stats")
async def admin_stats(callback: CallbackQuery) -> None:
    if await deny_callback(callback):
        return
    async with SessionLocal() as session:
        stats = await dashboard_stats(session)
    await callback.message.answer(
        "\n".join(
            [
                "<b>Статистика</b>",
                f"Сотрудников: {stats['employees']}",
                f"Активных: {stats['active']}",
                f"Рассылок: {stats['broadcasts']}",
                f"Вопросов: {stats['questions']}",
            ]
        ),
        reply_markup=back_to_admin_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:employees")
async def admin_employees(callback: CallbackQuery) -> None:
    if await deny_callback(callback):
        return
    async with SessionLocal() as session:
        employees = await list_employees(session)
    lines = ["<b>Сотрудники</b>", "ID нужен для рассылки выбранным сотрудникам.", ""]
    for employee in employees[:30]:
        role = " admin" if employee.is_admin else ""
        active = "active" if employee.is_active else "off"
        lines.append(f"#{employee.id} · {employee.full_name} · {employee.department} · {active}{role}")
    if len(employees) > 30:
        lines.append(f"\nПоказаны первые 30 из {len(employees)}.")
    if not employees:
        lines.append("Сотрудников пока нет.")
    await callback.message.answer("\n".join(lines), reply_markup=back_to_admin_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin:roles")
async def admin_roles(callback: CallbackQuery) -> None:
    if await deny_superuser_callback(callback):
        return
    async with SessionLocal() as session:
        employees = await list_employees(session)

    if not employees:
        await callback.message.answer("Сотрудников пока нет.", reply_markup=back_to_admin_keyboard())
        await callback.answer()
        return

    lines = [
        "<b>Управление ролями</b>",
        "Только суперпользователь может выдавать и снимать роль админа.",
        "",
    ]
    buttons = []
    for employee in employees[:20]:
        if employee.is_superuser:
            role = "superuser"
            button_text = f"Суперюзер #{employee.id}"
            callback_data = "admin:roles"
        elif employee.is_admin:
            role = "admin"
            button_text = f"Снять админа #{employee.id}"
            callback_data = f"admin:role-toggle:{employee.id}"
        else:
            role = "employee"
            button_text = f"Сделать админом #{employee.id}"
            callback_data = f"admin:role-toggle:{employee.id}"
        lines.append(f"#{employee.id} · {employee.full_name} · {employee.department} · {role}")
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=callback_data)])
    if len(employees) > 20:
        lines.append(f"\nПоказаны первые 20 из {len(employees)}.")
    buttons.append([InlineKeyboardButton(text="Назад в админку", callback_data="admin:menu")])
    await callback.message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:role-toggle:"))
async def admin_role_toggle(callback: CallbackQuery) -> None:
    if await deny_superuser_callback(callback):
        return
    employee_id = int(callback.data.rsplit(":", maxsplit=1)[-1])
    async with SessionLocal() as session:
        employee = await toggle_employee_admin(session, employee_id)
    if not employee:
        await callback.answer("Сотрудник не найден.", show_alert=True)
        return
    if employee.is_superuser:
        await callback.answer("Роль суперпользователя задается только через .env.", show_alert=True)
        return
    role = "админ" if employee.is_admin else "сотрудник"
    await callback.message.answer(f"Роль обновлена: #{employee.id} {employee.full_name} теперь {role}.")
    await admin_roles(callback)


@router.callback_query(F.data == "admin:broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    if await deny_callback(callback):
        return
    await state.set_state(AdminBroadcast.title)
    await callback.message.answer("Введите заголовок рассылки.")
    await callback.answer()


@router.message(AdminBroadcast.title)
async def admin_broadcast_title(message: Message, state: FSMContext) -> None:
    if await deny_message(message):
        return
    await state.update_data(title=message.text.strip())
    await state.set_state(AdminBroadcast.text)
    await message.answer("Введите текст рассылки.")


@router.message(AdminBroadcast.text)
async def admin_broadcast_text(message: Message, state: FSMContext) -> None:
    if await deny_message(message):
        return
    await state.update_data(text=message.text.strip())
    await message.answer("Выберите получателей. Вам самому и суперюзеру рассылка не отправится.", reply_markup=target_keyboard())


@router.callback_query(F.data.startswith("admin:btarget:"))
async def admin_broadcast_target(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if await deny_callback(callback):
        return
    target_type = callback.data.rsplit(":", maxsplit=1)[-1]
    if target_type == "all":
        await finish_admin_broadcast(callback.message, state, bot, "all")
        await callback.answer()
        return

    async with SessionLocal() as session:
        if target_type == "department":
            values = await distinct_values(session, "department")
            await state.set_state(AdminBroadcast.department)
            await callback.message.answer("Введите отдел.\n\nДоступные: " + (", ".join(values) if values else "пока нет"))
        elif target_type == "position":
            values = await distinct_values(session, "position")
            await state.set_state(AdminBroadcast.position)
            await callback.message.answer("Введите должность.\n\nДоступные: " + (", ".join(values) if values else "пока нет"))
        elif target_type == "selected":
            employees = await list_employees(session, active_only=True)
            await state.set_state(AdminBroadcast.selected)
            lines = ["Введите ID сотрудников через запятую.", ""]
            for employee in employees[:30]:
                lines.append(f"#{employee.id} · {employee.full_name} · {employee.department}")
            if len(employees) > 30:
                lines.append(f"\nПоказаны первые 30 из {len(employees)}.")
            await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.message(AdminBroadcast.department)
async def admin_broadcast_department(message: Message, state: FSMContext, bot: Bot) -> None:
    if await deny_message(message):
        return
    await finish_admin_broadcast(message, state, bot, "department", department=message.text.strip())


@router.message(AdminBroadcast.position)
async def admin_broadcast_position(message: Message, state: FSMContext, bot: Bot) -> None:
    if await deny_message(message):
        return
    await finish_admin_broadcast(message, state, bot, "position", position=message.text.strip())


@router.message(AdminBroadcast.selected)
async def admin_broadcast_selected(message: Message, state: FSMContext, bot: Bot) -> None:
    if await deny_message(message):
        return
    employee_ids = []
    for item in message.text.replace(" ", "").split(","):
        if item.isdigit():
            employee_ids.append(int(item))
    await finish_admin_broadcast(message, state, bot, "selected", employee_ids=employee_ids)


async def finish_admin_broadcast(
    message: Message,
    state: FSMContext,
    bot: Bot,
    target_type: str,
    department: str | None = None,
    position: str | None = None,
    employee_ids: list[int] | None = None,
) -> None:
    data = await state.get_data()
    await state.clear()
    async with SessionLocal() as session:
        recipients = await resolve_recipients(
            session,
            target_type,
            department,
            position,
            employee_ids,
            exclude_telegram_ids=[message.from_user.id],
        )
        if target_type == "department":
            target_value = department
        elif target_type == "position":
            target_value = position
        elif target_type == "selected":
            target_value = ",".join(str(item) for item in employee_ids or [])
        else:
            target_value = None
        broadcast = await create_broadcast(session, data["title"], data["text"], target_type, recipients, target_value)
        if recipients:
            await send_broadcast(session, bot, broadcast.id)
        deliveries = await delivery_rows(session, broadcast.id)
        stats = broadcast_stats(deliveries)

    await message.answer(
        "\n".join(
            [
                "<b>Рассылка создана</b>",
                f"ID: {broadcast.id}",
                f"Получателей: {stats['total']}",
                f"Отправлено: {stats['sent']}",
                f"Ошибок: {stats['failed']}",
            ]
        ),
        reply_markup=back_to_admin_keyboard(),
    )


@router.callback_query(F.data == "admin:questions")
async def admin_questions(callback: CallbackQuery) -> None:
    if await deny_callback(callback):
        return
    async with SessionLocal() as session:
        questions = await list_questions(session)
    if not questions:
        await callback.message.answer("Вопросов пока нет.", reply_markup=back_to_admin_keyboard())
        await callback.answer()
        return

    lines = ["<b>Вопросы сотрудников</b>"]
    buttons = []
    for item in questions[:10]:
        answer_mark = "ответ есть" if item.hr_answer else "без ответа"
        question = (item.question_text or "").replace("\n", " ")
        if len(question) > 80:
            question = question[:77] + "..."
        lines.append(f"\n#{item.id} · {item.employee.full_name} · {answer_mark}\n{question}")
        buttons.append([InlineKeyboardButton(text=f"Ответить #{item.id}", callback_data=f"admin:qanswer:{item.id}")])
    buttons.append([InlineKeyboardButton(text="Назад в админку", callback_data="admin:menu")])
    await callback.message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:qanswer:"))
async def admin_answer_start(callback: CallbackQuery, state: FSMContext) -> None:
    if await deny_callback(callback):
        return
    delivery_id = int(callback.data.rsplit(":", maxsplit=1)[-1])
    await state.set_state(AdminAnswer.answer)
    await state.update_data(delivery_id=delivery_id)
    await callback.message.answer(f"Введите ответ на вопрос #{delivery_id}.")
    await callback.answer()


@router.message(AdminAnswer.answer)
async def admin_answer_save(message: Message, state: FSMContext, bot: Bot) -> None:
    if await deny_message(message):
        return
    data = await state.get_data()
    await state.clear()
    delivery_id = int(data["delivery_id"])
    async with SessionLocal() as session:
        delivery = await save_hr_answer(session, delivery_id, message.text.strip())
    if not delivery:
        await message.answer("Вопрос не найден.", reply_markup=back_to_admin_keyboard())
        return
    await bot.send_message(
        delivery.employee.telegram_id,
        f"<b>HR жауабы</b>\n\n{escape(message.text.strip())}\n\nХабарлама: {escape(delivery.broadcast.title)}",
    )
    await message.answer("Ответ сохранен и отправлен сотруднику.", reply_markup=back_to_admin_keyboard())


@router.callback_query(F.data.startswith("resp:"))
async def handle_response(callback: CallbackQuery, state: FSMContext) -> None:
    _, delivery_id_raw, response_raw = callback.data.split(":", maxsplit=2)
    delivery_id = int(delivery_id_raw)
    response = ResponseType(response_raw)
    async with SessionLocal() as session:
        delivery = await set_delivery_response(session, delivery_id, callback.from_user.id, response)
    if not delivery:
        await callback.answer("Это сообщение не относится к вашему аккаунту.", show_alert=True)
        return
    if response == ResponseType.QUESTION:
        await state.set_state(Feedback.question)
        await state.update_data(delivery_id=delivery_id)
        await callback.message.answer("Напишите ваш вопрос в этот чат.")
        await callback.answer()
        return
    text = "Ответ сохранен: Таныстым" if response == ResponseType.ACKNOWLEDGED else "Ответ сохранен: Келістім"
    await callback.answer(text)
    await callback.message.answer(text)


@router.message(Feedback.question)
async def feedback_question(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    delivery_id = int(data["delivery_id"])
    async with SessionLocal() as session:
        saved = await save_question(session, delivery_id, message.from_user.id, message.text.strip())
    await state.clear()
    if saved:
        await message.answer("Ваш вопрос отправлен HR. Ответ придет в этот чат.")
    else:
        await message.answer("Не удалось сохранить вопрос. Обратитесь в HR.")


@router.message()
async def fallback(message: Message) -> None:
    async with SessionLocal() as session:
        employee = await get_employee_by_telegram_id(session, message.from_user.id)
    if not employee:
        await message.answer("Для регистрации нажмите /start.")
        return
    if employee.is_admin:
        await message.answer("Для админ-панели используйте /admin. Для ответов на рассылки используйте кнопки под сообщениями.")
        return
    await message.answer("Для ответов на HR-сообщения используйте кнопки под сообщениями.")

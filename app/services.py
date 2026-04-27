from collections import Counter
from datetime import datetime
from html import escape
from typing import Iterable

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from passlib.context import CryptContext
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import AdminUser, Broadcast, DeliveryStatus, Employee, MessageDelivery, ResponseType

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

RESPONSE_LABELS = {
    ResponseType.ACKNOWLEDGED: "Таныстым",
    ResponseType.AGREED: "Келістім",
    ResponseType.QUESTION: "Сұрағым бар",
}


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def configured_superuser_telegram_id() -> int | None:
    raw_value = (settings.superuser_telegram_id or "").strip()
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except ValueError:
        return None


def is_configured_superuser(telegram_id: int) -> bool:
    superuser_id = configured_superuser_telegram_id()
    return superuser_id is not None and telegram_id == superuser_id


def apply_superuser_role(employee: Employee) -> bool:
    if not is_configured_superuser(employee.telegram_id):
        return False
    employee.is_active = True
    employee.is_admin = True
    employee.is_superuser = True
    return True


async def seed_admin(session: AsyncSession) -> None:
    result = await session.scalar(select(AdminUser).where(AdminUser.username == settings.admin_username))
    if result:
        return
    session.add(
        AdminUser(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            role="admin",
        )
    )
    await session.commit()


async def authenticate_admin(session: AsyncSession, username: str, password: str) -> AdminUser | None:
    user = await session.scalar(select(AdminUser).where(AdminUser.username == username))
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


async def get_employee_by_telegram_id(session: AsyncSession, telegram_id: int) -> Employee | None:
    return await session.scalar(select(Employee).where(Employee.telegram_id == telegram_id))


async def register_employee(
    session: AsyncSession,
    telegram_id: int,
    full_name: str,
    department: str,
    position: str,
    phone: str | None,
    employee_no: str | None,
) -> Employee:
    employee = await get_employee_by_telegram_id(session, telegram_id)
    if employee:
        employee.full_name = full_name
        employee.department = department
        employee.position = position
        employee.phone = phone
        employee.employee_no = employee_no
        employee.is_active = True
    else:
        employee = Employee(
            telegram_id=telegram_id,
            full_name=full_name,
            department=department,
            position=position,
            phone=phone,
            employee_no=employee_no,
        )
        session.add(employee)
    apply_superuser_role(employee)
    await session.commit()
    await session.refresh(employee)
    return employee


def employee_query(
    department: str | None = None,
    position: str | None = None,
    active_only: bool = False,
    search: str | None = None,
) -> Select[tuple[Employee]]:
    query = select(Employee).order_by(Employee.registered_at.desc())
    if department:
        query = query.where(Employee.department == department)
    if position:
        query = query.where(Employee.position == position)
    if active_only:
        query = query.where(Employee.is_active.is_(True))
    if search:
        like = f"%{search}%"
        query = query.where(
            Employee.full_name.ilike(like)
            | Employee.department.ilike(like)
            | Employee.position.ilike(like)
            | Employee.phone.ilike(like)
            | Employee.employee_no.ilike(like)
        )
    return query


async def list_employees(
    session: AsyncSession,
    department: str | None = None,
    position: str | None = None,
    active_only: bool = False,
    search: str | None = None,
) -> list[Employee]:
    return list((await session.scalars(employee_query(department, position, active_only, search))).all())


async def distinct_values(session: AsyncSession, column_name: str) -> list[str]:
    column = getattr(Employee, column_name)
    result = await session.scalars(select(column).where(column.is_not(None)).distinct().order_by(column))
    return [value for value in result.all() if value]


async def toggle_employee(session: AsyncSession, employee_id: int) -> Employee | None:
    employee = await session.get(Employee, employee_id)
    if not employee:
        return None
    employee.is_active = not employee.is_active
    await session.commit()
    await session.refresh(employee)
    return employee


async def toggle_employee_admin(session: AsyncSession, employee_id: int) -> Employee | None:
    employee = await session.get(Employee, employee_id)
    if not employee:
        return None
    if employee.is_superuser or apply_superuser_role(employee):
        await session.commit()
        await session.refresh(employee)
        return employee
    employee.is_admin = not employee.is_admin
    await session.commit()
    await session.refresh(employee)
    return employee


async def set_employee_admin(session: AsyncSession, employee_id: int, is_admin: bool) -> Employee | None:
    employee = await session.get(Employee, employee_id)
    if not employee:
        return None
    if employee.is_superuser or apply_superuser_role(employee):
        await session.commit()
        await session.refresh(employee)
        return employee
    employee.is_admin = is_admin
    await session.commit()
    await session.refresh(employee)
    return employee


async def get_admin_employee(session: AsyncSession, telegram_id: int) -> Employee | None:
    employee = await get_employee_by_telegram_id(session, telegram_id)
    if employee and apply_superuser_role(employee):
        await session.commit()
        await session.refresh(employee)
    if not employee or not employee.is_active or not employee.is_admin:
        return None
    return employee


async def get_superuser_employee(session: AsyncSession, telegram_id: int) -> Employee | None:
    employee = await get_admin_employee(session, telegram_id)
    if not employee or not employee.is_superuser:
        return None
    return employee


async def sync_superuser_role(session: AsyncSession) -> Employee | None:
    superuser_id = configured_superuser_telegram_id()
    if superuser_id is None:
        return None
    employee = await get_employee_by_telegram_id(session, superuser_id)
    if not employee:
        return None
    apply_superuser_role(employee)
    await session.commit()
    await session.refresh(employee)
    return employee


async def resolve_recipients(
    session: AsyncSession,
    target_type: str,
    department: str | None = None,
    position: str | None = None,
    employee_ids: Iterable[int] | None = None,
    exclude_telegram_ids: Iterable[int] | None = None,
) -> list[Employee]:
    query = select(Employee).where(Employee.is_active.is_(True), Employee.is_superuser.is_(False))
    excluded_ids = [int(item) for item in (exclude_telegram_ids or []) if str(item).strip()]
    if excluded_ids:
        query = query.where(Employee.telegram_id.not_in(excluded_ids))
    if target_type == "department":
        if not department:
            return []
        query = query.where(Employee.department == department)
    elif target_type == "position":
        if not position:
            return []
        query = query.where(Employee.position == position)
    elif target_type == "selected":
        ids = [int(item) for item in (employee_ids or []) if str(item).strip()]
        if not ids:
            return []
        query = query.where(Employee.id.in_(ids))
    return list((await session.scalars(query.order_by(Employee.full_name))).all())


async def create_broadcast(
    session: AsyncSession,
    title: str,
    text: str,
    target_type: str,
    recipients: list[Employee],
    target_value: str | None = None,
) -> Broadcast:
    broadcast = Broadcast(title=title, text=text, target_type=target_type, target_value=target_value)
    session.add(broadcast)
    await session.flush()
    for employee in recipients:
        session.add(MessageDelivery(broadcast_id=broadcast.id, employee_id=employee.id))
    await session.commit()
    await session.refresh(broadcast)
    return broadcast


def response_keyboard(delivery_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Таныстым", callback_data=f"resp:{delivery_id}:acknowledged"),
                InlineKeyboardButton(text="Келістім", callback_data=f"resp:{delivery_id}:agreed"),
            ],
            [InlineKeyboardButton(text="Сұрағым бар", callback_data=f"resp:{delivery_id}:question")],
        ]
    )


async def send_broadcast(session: AsyncSession, bot: Bot, broadcast_id: int) -> None:
    broadcast = await session.scalar(
        select(Broadcast)
        .where(Broadcast.id == broadcast_id)
        .options(selectinload(Broadcast.deliveries).selectinload(MessageDelivery.employee))
    )
    if not broadcast:
        return

    for delivery in broadcast.deliveries:
        employee = delivery.employee
        try:
            message = await bot.send_message(
                chat_id=employee.telegram_id,
                text=f"<b>{escape(broadcast.title)}</b>\n\n{escape(broadcast.text)}",
                reply_markup=response_keyboard(delivery.id),
            )
            delivery.telegram_message_id = message.message_id
            delivery.status = DeliveryStatus.SENT
            delivery.sent_at = datetime.utcnow()
            delivery.error = None
        except Exception as exc:  # Telegram API errors are stored for HR visibility.
            delivery.status = DeliveryStatus.FAILED
            delivery.error = str(exc)
    broadcast.sent_at = datetime.utcnow()
    await session.commit()


async def set_delivery_response(
    session: AsyncSession,
    delivery_id: int,
    telegram_id: int,
    response: ResponseType,
) -> MessageDelivery | None:
    delivery = await session.scalar(
        select(MessageDelivery)
        .where(MessageDelivery.id == delivery_id)
        .options(selectinload(MessageDelivery.employee), selectinload(MessageDelivery.broadcast))
    )
    if not delivery or delivery.employee.telegram_id != telegram_id:
        return None
    delivery.response = response
    delivery.response_at = datetime.utcnow()
    if response != ResponseType.QUESTION:
        delivery.question_text = None
        delivery.question_at = None
    await session.commit()
    await session.refresh(delivery)
    return delivery


async def save_question(session: AsyncSession, delivery_id: int, telegram_id: int, question: str) -> bool:
    delivery = await session.scalar(
        select(MessageDelivery)
        .where(MessageDelivery.id == delivery_id)
        .options(selectinload(MessageDelivery.employee))
    )
    if not delivery or delivery.employee.telegram_id != telegram_id:
        return False
    delivery.response = ResponseType.QUESTION
    delivery.response_at = delivery.response_at or datetime.utcnow()
    delivery.question_text = question
    delivery.question_at = datetime.utcnow()
    await session.commit()
    return True


async def save_hr_answer(session: AsyncSession, delivery_id: int, answer: str) -> MessageDelivery | None:
    delivery = await session.scalar(
        select(MessageDelivery)
        .where(MessageDelivery.id == delivery_id)
        .options(selectinload(MessageDelivery.employee), selectinload(MessageDelivery.broadcast))
    )
    if not delivery:
        return None
    delivery.hr_answer = answer
    delivery.answered_at = datetime.utcnow()
    await session.commit()
    await session.refresh(delivery)
    return delivery


async def get_broadcast(session: AsyncSession, broadcast_id: int) -> Broadcast | None:
    return await session.scalar(
        select(Broadcast)
        .where(Broadcast.id == broadcast_id)
        .options(selectinload(Broadcast.deliveries).selectinload(MessageDelivery.employee))
    )


async def list_broadcasts(session: AsyncSession) -> list[Broadcast]:
    return list((await session.scalars(select(Broadcast).order_by(Broadcast.created_at.desc()))).all())


async def delivery_rows(session: AsyncSession, broadcast_id: int) -> list[MessageDelivery]:
    result = await session.scalars(
        select(MessageDelivery)
        .where(MessageDelivery.broadcast_id == broadcast_id)
        .options(selectinload(MessageDelivery.employee), selectinload(MessageDelivery.broadcast))
        .order_by(MessageDelivery.id)
    )
    return list(result.all())


def broadcast_stats(deliveries: list[MessageDelivery]) -> dict[str, int]:
    responses = Counter(delivery.response for delivery in deliveries)
    sent = sum(1 for delivery in deliveries if delivery.status == DeliveryStatus.SENT)
    failed = sum(1 for delivery in deliveries if delivery.status == DeliveryStatus.FAILED)
    answered = sum(1 for delivery in deliveries if delivery.response is not None)
    return {
        "total": len(deliveries),
        "sent": sent,
        "failed": failed,
        "acknowledged": responses[ResponseType.ACKNOWLEDGED],
        "agreed": responses[ResponseType.AGREED],
        "question": responses[ResponseType.QUESTION],
        "unanswered": len(deliveries) - answered,
    }


async def dashboard_stats(session: AsyncSession) -> dict[str, int]:
    employee_count = await session.scalar(select(func.count(Employee.id)))
    active_count = await session.scalar(select(func.count(Employee.id)).where(Employee.is_active.is_(True)))
    broadcast_count = await session.scalar(select(func.count(Broadcast.id)))
    question_count = await session.scalar(
        select(func.count(MessageDelivery.id)).where(MessageDelivery.question_text.is_not(None))
    )
    return {
        "employees": employee_count or 0,
        "active": active_count or 0,
        "broadcasts": broadcast_count or 0,
        "questions": question_count or 0,
    }


async def list_questions(session: AsyncSession) -> list[MessageDelivery]:
    result = await session.scalars(
        select(MessageDelivery)
        .where(MessageDelivery.question_text.is_not(None))
        .options(selectinload(MessageDelivery.employee), selectinload(MessageDelivery.broadcast))
        .order_by(MessageDelivery.question_at.desc())
    )
    return list(result.all())


def response_label(value: ResponseType | None) -> str:
    if value is None:
        return "Жауап жоқ"
    return RESPONSE_LABELS[value]

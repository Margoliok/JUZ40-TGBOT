from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ResponseType(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    AGREED = "agreed"
    QUESTION = "question"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    department: Mapped[str] = mapped_column(String(120), index=True)
    position: Mapped[str] = mapped_column(String(120), index=True)
    phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    employee_no: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    deliveries: Mapped[list["MessageDelivery"]] = relationship(back_populates="employee")


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(String(50), default="all")
    target_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    deliveries: Mapped[list["MessageDelivery"]] = relationship(
        back_populates="broadcast", cascade="all, delete-orphan"
    )


class MessageDelivery(Base):
    __tablename__ = "message_deliveries"
    __table_args__ = (UniqueConstraint("broadcast_id", "employee_id", name="uq_broadcast_employee"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(ForeignKey("broadcasts.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[DeliveryStatus] = mapped_column(Enum(DeliveryStatus), default=DeliveryStatus.PENDING)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    response: Mapped[ResponseType | None] = mapped_column(Enum(ResponseType), nullable=True, index=True)
    response_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    question_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hr_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    broadcast: Mapped[Broadcast] = relationship(back_populates="deliveries")
    employee: Mapped[Employee] = relationship(back_populates="deliveries")

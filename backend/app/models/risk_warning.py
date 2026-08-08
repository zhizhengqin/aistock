from sqlalchemy import Integer, String, DateTime, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class RiskWarning(Base, TimestampMixin):
    __tablename__ = "risk_warnings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    stock_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    level: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    message: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    value: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)

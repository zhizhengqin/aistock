from sqlalchemy import Integer, String, Float, Boolean, DateTime, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class MonitorConfig(Base, TimestampMixin):
    __tablename__ = "monitor_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    stock_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    target_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    stop_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    profit_pct: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    loss_pct: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    interval_min: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    channels: Mapped[str] = mapped_column(String(128), default="in_app", nullable=False)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    last_checked_at: Mapped[str] = mapped_column(String(32), nullable=True)

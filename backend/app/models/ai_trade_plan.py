from sqlalchemy import Integer, String, Float, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class AiTradePlan(Base, TimestampMixin):
    """F-08-04: AI-generated trading plan for a monitored stock."""
    __tablename__ = "ai_trade_plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    config_id: Mapped[int] = mapped_column(Integer, ForeignKey("monitor_configs.id"), nullable=True, index=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    stock_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    action: Mapped[str] = mapped_column(String(16), default="hold", nullable=False)
    suggested_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    target_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    plan_json: Mapped[dict] = mapped_column(JSON, nullable=True)

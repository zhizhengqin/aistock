from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class AiDecisionRecord(Base, TimestampMixin):
    """F-08-04: Log of each AI decision made during monitoring."""
    __tablename__ = "ai_decision_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    config_id: Mapped[int] = mapped_column(Integer, ForeignKey("monitor_configs.id"), nullable=True, index=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    stock_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    decision_type: Mapped[str] = mapped_column(String(32), default="monitor_check", nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    detail_json: Mapped[dict] = mapped_column(JSON, nullable=True)

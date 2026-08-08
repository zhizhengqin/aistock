from datetime import datetime
from sqlalchemy import Integer, String, DateTime, Text, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import JSON
from app.models.base import Base, TimestampMixin


class TaskRecord(Base, TimestampMixin):
    __tablename__ = "task_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    ref_id: Mapped[int] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

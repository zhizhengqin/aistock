from sqlalchemy import Integer, String, DateTime, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import JSON
from app.models.base import Base, TimestampMixin


class MainForceRun(Base, TimestampMixin):
    __tablename__ = "main_force_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    run_date: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    candidates_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    filtered_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    recommended_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    excluded_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    token_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    task_id: Mapped[int] = mapped_column(Integer, nullable=True)
    analysis_json: Mapped[dict] = mapped_column(JSON, nullable=True)

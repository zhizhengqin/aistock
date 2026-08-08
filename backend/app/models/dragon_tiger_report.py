from sqlalchemy import Integer, String, DateTime, Text, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import JSON
from app.models.base import Base, TimestampMixin


class DragonTigerReport(Base, TimestampMixin):
    __tablename__ = "dragon_tiger_reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    period_days: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    stats_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    top_stocks_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    institutions_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    analysis_text: Mapped[str] = mapped_column(Text, nullable=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=True)

from sqlalchemy import Integer, String, DateTime, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import JSON
from app.models.base import Base, TimestampMixin


class SectorReport(Base, TimestampMixin):
    __tablename__ = "sector_reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    report_date: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    bull_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    bear_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    neutral_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    rotation_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    agents_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=True)

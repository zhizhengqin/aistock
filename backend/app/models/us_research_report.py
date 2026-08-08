from sqlalchemy import String, Text
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class UsResearchReport(Base, TimestampMixin):
    __tablename__ = "us_research_reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_date: Mapped[str] = mapped_column(String(10), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="success", nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=True)
    data_status: Mapped[dict] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)

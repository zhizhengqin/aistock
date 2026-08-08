from sqlalchemy import Integer, String, Float, DateTime, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import JSON
from app.models.base import Base, TimestampMixin


class AnalysisReport(Base, TimestampMixin):
    __tablename__ = "analysis_reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    stock_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    rating: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    report_json: Mapped[dict] = mapped_column(JSON, nullable=True)

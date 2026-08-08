from sqlalchemy import Integer, Float, DateTime, Text, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import JSON
from app.models.base import Base, TimestampMixin


class PortfolioReport(Base, TimestampMixin):
    __tablename__ = "portfolio_reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    health_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    diagnosis_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=True)

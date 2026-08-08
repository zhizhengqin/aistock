from datetime import date
from sqlalchemy import String, Integer, Date, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class UsageLog(Base):
    """Per-user per-feature daily usage counter (F-12-02/F-12-04)."""

    __tablename__ = "usage_logs"
    __table_args__ = (
        UniqueConstraint("user_id", "feature", "used_on", name="uq_usage_user_feature_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    used_on: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

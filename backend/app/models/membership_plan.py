from sqlalchemy import String, Integer, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class MembershipPlan(Base, TimestampMixin):
    """Membership tier definition with per-feature quota matrix (F-12-01)."""

    __tablename__ = "membership_plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    price_monthly_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    price_yearly_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # {feature_key: daily_limit} — 0 = locked, -1 = unlimited, n = n times/day
    quotas: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

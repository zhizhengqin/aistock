from sqlalchemy import Integer, String, Float, Boolean, DateTime, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class PortfolioStock(Base, TimestampMixin):
    __tablename__ = "portfolio_stocks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    stock_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    shares: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    auto_monitor: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    market_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    profit_loss: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    profit_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    industry: Mapped[str] = mapped_column(String(64), default="", nullable=False)

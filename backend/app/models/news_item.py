from datetime import datetime
from sqlalchemy import String, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class NewsItem(Base, TimestampMixin):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    url_hash: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    sentiment: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="综合", nullable=False)
    industries: Mapped[str] = mapped_column(String(256), default="", nullable=False)

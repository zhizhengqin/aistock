"""RSS/Atom news source used for optional global news collection."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from app.datahub.contracts import Capability, DataQuality, DataResult, NewsItem
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.base import ProviderAdapter, translate_provider_error
from app.datahub.validators import validate_payload


FEEDS = {
    "华尔街见闻": "https://dedicated.wallstreetcn.com/rss.xml",
    "FT中文网": "https://www.ftchinese.com/rss/news",
}


class RssProvider(ProviderAdapter):
    name = "rss"

    def __init__(self, *, http_client: Any | None = None, limiter=None) -> None:
        super().__init__(limiter=limiter)
        self.http_client = http_client

    async def fetch(self, capability: Capability | str, params: dict[str, Any]) -> DataResult:
        capability = Capability(capability)
        if capability is not Capability.STOCK_NEWS:
            raise DataHubError(DataHubErrorCode.UNSUPPORTED, "RSS 仅支持新闻能力", provider=self.name)
        source = str(params.get("source") or "").strip()
        url = FEEDS.get(source)
        if not url:
            raise DataHubError(DataHubErrorCode.UNSUPPORTED, "该新闻来源未配置 RSS 地址", provider=self.name)
        try:
            text = await self.run_sync(lambda: self._get_text(url))
            rows = _parse_feed(text, source)[: max(1, min(int(params.get("limit", 30)), 100))]
            typed = [NewsItem.model_validate(row) for row in rows]
            count = validate_payload(capability, typed)
            data_at = max((item.date for item in typed if isinstance(item.date, datetime)), default=None)
            if data_at is None:
                raise DataHubError(DataHubErrorCode.STALE_INVALID, "RSS 新闻缺少可信发布时间", provider=self.name)
            return DataResult(data=typed, capability=capability, provider=self.name, data_at=data_at, quality=DataQuality(valid=True, rows=count))
        except DataHubError:
            raise
        except Exception as exc:
            raise translate_provider_error(exc, provider=self.name) from None

    def _get_text(self, url: str) -> str:
        client = self.http_client
        close = False
        if client is None:
            import httpx

            client = httpx.Client()
            close = True
        try:
            response = client.get(url, timeout=15)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return str(getattr(response, "text", ""))
        finally:
            if close and hasattr(client, "close"):
                client.close()


def _parse_feed(text: str, source: str) -> list[dict[str, Any]]:
    root = ET.fromstring(text.encode("utf-8"))
    rows: list[dict[str, Any]] = []
    for item in root.iter("item"):
        title = _text(item, "title")
        if not title:
            continue
        published = _parse_date(_text(item, "pubDate") or _text(item, "date") or _text(item, "published"))
        if published is None:
            continue
        rows.append({"title": title, "content": re.sub(r"<[^>]+>", "", _text(item, "description"))[:500], "date": published, "source": source, "url": _text(item, "link") or None})
    return rows


def _text(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def _parse_date(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


__all__ = ["FEEDS", "RssProvider"]

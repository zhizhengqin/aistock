"""Monitor engine: check user monitor configs against latest prices.

Runs as a periodic task. For each active config, fetches latest price,
compares against target/stop/entry prices, creates notifications when triggered.
"""

from datetime import datetime, timezone
import anyio
from app.core.database import SessionLocal
from app.core.logger import logger
from app.datahub.contracts import Capability, DataResult
from app.datahub.consumer import get_optional_kpl, get_stock_info
from app.datahub.errors import DataHubError
from app.models.monitor_config import MonitorConfig
from app.models.monitor_notification import MonitorNotification


def _is_trading_hours() -> bool:
    """Check if current time is within A-share trading hours (9:25-15:05 Beijing)."""
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    weekday = now.weekday()
    if weekday >= 5:
        return False
    if hour < 9 or hour > 15:
        return False
    if hour == 9 and minute < 25:
        return False
    if hour == 15 and minute > 5:
        return False
    return True


async def _fetch_optional_auction(stock_code: str) -> DataResult | None:
    """Read auction context only when the optional KPL route is enabled."""

    try:
        return await get_optional_kpl(Capability.MARKET_AUCTION_OPEN, {"ts_code": stock_code})
    except DataHubError:
        logger.warning("Optional KPL auction data unavailable for monitor %s", stock_code)
        return None


def run_monitor_check():
    """Check all active monitor configs, create notifications for triggered alerts."""
    if not _is_trading_hours():
        logger.info("Monitor check skipped: outside trading hours")
        return 0

    db = SessionLocal()
    triggered_count = 0
    try:
        configs = db.query(MonitorConfig).filter(MonitorConfig.status == "running").all()
        for config in configs:
            try:
                info = anyio.run(get_stock_info, config.stock_code).data.model_dump(mode="json")
                current_price = info.get("price", 0)
                if current_price <= 0:
                    continue
                auction_result = anyio.run(_fetch_optional_auction, config.stock_code)
                auction_note = ""
                if auction_result is not None and auction_result.data:
                    auction = auction_result.data[0].model_dump(mode="json")
                    auction_note = f"；竞价参考价{auction.get('open', 0)}"

                # Check target price (take profit)
                if config.target_price > 0 and current_price >= config.target_price:
                    Notification_create_notification(db, config, current_price,
                        "take_profit", f"{config.stock_name}触发止盈",
                        f"{config.stock_name}({config.stock_code})当前价{current_price}已达目标价{config.target_price}{auction_note}")
                    triggered_count += 1

                # Check stop loss
                if config.stop_price > 0 and current_price <= config.stop_price:
                    Notification_create_notification(db, config, current_price,
                        "stop_loss", f"{config.stock_name}触发止损",
                        f"{config.stock_name}({config.stock_code})当前价{current_price}已跌破止损价{config.stop_price}{auction_note}")
                    triggered_count += 1

                # Check profit pct from entry
                if config.entry_price > 0 and config.profit_pct > 0:
                    pct = (current_price - config.entry_price) / config.entry_price * 100
                    if pct >= config.profit_pct:
                        Notification_create_notification(db, config, current_price,
                            "profit_alert", f"{config.stock_name}涨幅达标",
                            f"{config.stock_name}({config.stock_code})当前涨幅{pct:.1f}%，达到设定{config.profit_pct}%{auction_note}")
                        triggered_count += 1

                # Check loss pct from entry
                if config.entry_price > 0 and config.loss_pct > 0:
                    pct = (current_price - config.entry_price) / config.entry_price * 100
                    if pct <= -config.loss_pct:
                        Notification_create_notification(db, config, current_price,
                            "loss_alert", f"{config.stock_name}跌幅达标",
                            f"{config.stock_name}({config.stock_code})当前跌幅{pct:.1f}%，达到设定-{config.loss_pct}%{auction_note}")
                        triggered_count += 1

                # Update last checked timestamp
                config.last_checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            except Exception as e:
                logger.warning(f"Monitor check failed for config {config.id}: {e}")

        db.commit()
        logger.info(f"Monitor check done: {triggered_count} notifications triggered")
        return triggered_count
    except Exception as e:
        logger.error(f"Monitor check error: {e}")
        return 0
    finally:
        db.close()


def Notification_create_notification(db, config: MonitorConfig, current_price: float,
                                     ntype: str, title: str, content: str):
    """Create a monitor notification."""
    # Deduplicate: don't create duplicate notification within same day
    existing = db.query(MonitorNotification).filter(
        MonitorNotification.config_id == config.id,
        MonitorNotification.ntype == ntype,
        MonitorNotification.status == "pending",
    ).first()
    if existing:
        return

    n = MonitorNotification(
        config_id=config.id,
        user_id=config.user_id,
        stock_code=config.stock_code,
        stock_name=config.stock_name,
        ntype=ntype,
        title=title,
        content=content,
        status="pending",
    )
    db.add(n)
    db.commit()
logger.info("Monitor engine loaded")

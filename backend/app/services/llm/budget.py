"""PostgreSQL-backed daily token reservations and settlement."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.llm_config import LlmRuntimeSetting
from app.models.llm_execution import LlmDailyBudget, LlmTokenReservation
from app.services.llm.errors import LlmError


BEIJING = ZoneInfo("Asia/Shanghai")
DEFAULT_DAILY_TOKEN_LIMIT = 2_000_000
DEFAULT_LEASE_SECONDS = 300


def _error(code: str, message: str) -> LlmError:
    error = LlmError(message, code=code)
    error.retryable = code in {"llm_budget_exhausted", "llm_budget_locked"}
    error.user_message = message
    error.confirmed_unsent = True
    error.may_have_sent = False
    return error


class TokenBudgetService:
    """Durable budget ledger with row-level serialization.

    ``db`` can be a live SQLAlchemy ``Session`` or a session factory.  Each
    public operation commits its own short transaction; no network operation
    is ever performed while one of these transactions is open.
    """

    def __init__(
        self,
        db: Session | Callable[[], Session],
        daily_token_limit: int | None = None,
        *,
        limit: int | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        clock: Callable[[], datetime] | None = None,
        admin_notifier: Callable[[str], Any] | None = None,
    ) -> None:
        if daily_token_limit is not None and limit is not None and daily_token_limit != limit:
            raise ValueError("daily_token_limit 与 limit 不能同时指定")
        configured_limit = daily_token_limit if daily_token_limit is not None else limit
        if configured_limit is not None and int(configured_limit) <= 0:
            raise ValueError("每日 Token 限额必须为正整数")
        if int(lease_seconds) <= 0:
            raise ValueError("额度租约必须为正秒数")
        self.db = db
        self.daily_token_limit = int(configured_limit) if configured_limit is not None else None
        self.lease_seconds = int(lease_seconds)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.admin_notifier = admin_notifier

    @contextmanager
    def _session(self):
        own = not isinstance(self.db, Session)
        session = self.db() if own else self.db
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            if own:
                session.close()

    def _now(self, value: datetime | None = None) -> datetime:
        current = value or self.clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current

    def _budget_date(self, value: date | datetime | None = None) -> date:
        if value is None:
            return self._now().astimezone(BEIJING).date()
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(BEIJING).date()
        return value

    def _runtime_setting(self, session: Session) -> LlmRuntimeSetting:
        setting = session.execute(
            select(LlmRuntimeSetting).where(LlmRuntimeSetting.id == 1).with_for_update()
        ).scalar_one_or_none()
        if setting is None:
            values = {
                "id": 1,
                "daily_token_limit": self.daily_token_limit or DEFAULT_DAILY_TOKEN_LIMIT,
                "budget_locked": False,
                "version": 1,
            }
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                session.execute(
                    pg_insert(LlmRuntimeSetting).values(**values).on_conflict_do_nothing(
                        index_elements=["id"]
                    )
                )
            elif dialect == "sqlite":
                session.execute(
                    sqlite_insert(LlmRuntimeSetting).values(**values).on_conflict_do_nothing(
                        index_elements=["id"]
                    )
                )
            else:
                session.add(LlmRuntimeSetting(**values))
            session.flush()
            setting = session.execute(
                select(LlmRuntimeSetting).where(LlmRuntimeSetting.id == 1).with_for_update()
            ).scalar_one()
        return setting

    def _daily_row(self, session: Session, budget_date: date) -> LlmDailyBudget:
        row = session.execute(
            select(LlmDailyBudget)
            .where(LlmDailyBudget.budget_date == budget_date)
            .with_for_update()
        ).scalar_one_or_none()
        if row is not None:
            return row
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            session.execute(
                pg_insert(LlmDailyBudget)
                .values(budget_date=budget_date, reserved_tokens=0, settled_tokens=0)
                .on_conflict_do_nothing(index_elements=["budget_date"])
            )
        elif dialect == "sqlite":
            session.execute(
                sqlite_insert(LlmDailyBudget)
                .values(budget_date=budget_date, reserved_tokens=0, settled_tokens=0)
                .on_conflict_do_nothing(index_elements=["budget_date"])
            )
        else:
            session.add(LlmDailyBudget(budget_date=budget_date))
        session.flush()
        row = session.execute(
            select(LlmDailyBudget)
            .where(LlmDailyBudget.budget_date == budget_date)
            .with_for_update()
        ).scalar_one()
        return row

    def _effective_limit(self, setting: LlmRuntimeSetting) -> int:
        return self.daily_token_limit or int(setting.daily_token_limit)

    def reserve(
        self,
        tokens: int,
        *,
        step_key: str,
        task_id: int | None = None,
        budget_date: date | datetime | None = None,
        now: datetime | None = None,
        lease_expires_at: datetime | None = None,
    ) -> LlmTokenReservation:
        """Atomically reserve an upper bound for exactly one physical call."""

        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens <= 0:
            raise _error("llm_reservation_invalid", "大模型额度预留量必须为正整数")
        if not isinstance(step_key, str) or not step_key.strip():
            raise _error("llm_step_invalid", "大模型调用步骤不能为空")
        day = self._budget_date(budget_date if budget_date is not None else now)
        current = self._now(now)
        expires = lease_expires_at or (current + timedelta(seconds=self.lease_seconds))
        with self._session() as session:
            setting = self._runtime_setting(session)
            if bool(setting.budget_locked):
                raise _error("llm_budget_locked", "大模型额度已锁定，请联系管理员处理")
            ledger = self._daily_row(session, day)
            limit = self._effective_limit(setting)
            used = int(ledger.reserved_tokens) + int(ledger.settled_tokens)
            if used + tokens > limit:
                raise _error("llm_budget_exhausted", "今日大模型 Token 额度不足")
            ledger.reserved_tokens += tokens
            reservation = LlmTokenReservation(
                task_id=task_id,
                step_key=step_key,
                budget_date=day,
                reserved_tokens=tokens,
                lease_expires_at=expires,
                status="reserved",
            )
            session.add(reservation)
            session.flush()
            # Prevent expire-on-commit from detaching an unusable result.
            session.refresh(reservation)
            session.expunge(reservation)
            return reservation

    def _get_reservation(self, session: Session, reservation: str | LlmTokenReservation) -> LlmTokenReservation:
        reservation_id = reservation.id if isinstance(reservation, LlmTokenReservation) else reservation
        row = session.execute(
            select(LlmTokenReservation)
            .where(LlmTokenReservation.id == reservation_id)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise _error("llm_reservation_missing", "大模型额度预留不存在")
        return row

    def settle(
        self,
        reservation: str | LlmTokenReservation,
        actual_tokens: int | None = None,
        *,
        actual: int | None = None,
        unknown: bool = False,
    ) -> LlmTokenReservation:
        """Settle actual usage, conservatively using the upper bound if absent."""

        if actual_tokens is not None and actual is not None and actual_tokens != actual:
            raise _error("llm_usage_invalid", "大模型用量参数冲突")
        if actual_tokens is None:
            actual_tokens = actual
        with self._session() as session:
            setting = self._runtime_setting(session)
            row = self._get_reservation(session, reservation)
            if row.status != "reserved":
                return row
            if actual_tokens is not None and (
                not isinstance(actual_tokens, int) or isinstance(actual_tokens, bool) or actual_tokens < 0
            ):
                raise _error("llm_usage_invalid", "大模型用量数据无效")
            actual = int(row.reserved_tokens) if unknown or actual_tokens is None else int(actual_tokens)
            ledger = session.execute(
                select(LlmDailyBudget)
                .where(LlmDailyBudget.budget_date == row.budget_date)
                .with_for_update()
            ).scalar_one()
            reserved = int(row.reserved_tokens)
            ledger.reserved_tokens = max(0, int(ledger.reserved_tokens) - reserved)
            ledger.settled_tokens += actual
            row.settled_tokens = actual
            row.status = "settled"
            if actual > reserved:
                setting.budget_locked = True
                if self.admin_notifier is not None:
                    try:
                        self.admin_notifier("大模型实际用量超过预留，额度已锁定")
                    except Exception:
                        # Notification failure must not roll back the durable
                        # lock or settlement.
                        pass
            session.flush()
            session.refresh(row)
            session.expunge(row)
            return row

    def release(self, reservation: str | LlmTokenReservation) -> LlmTokenReservation:
        """Release a reservation that is confirmed unsent."""

        with self._session() as session:
            row = self._get_reservation(session, reservation)
            if row.status != "reserved":
                return row
            ledger = session.execute(
                select(LlmDailyBudget)
                .where(LlmDailyBudget.budget_date == row.budget_date)
                .with_for_update()
            ).scalar_one()
            ledger.reserved_tokens = max(0, int(ledger.reserved_tokens) - int(row.reserved_tokens))
            row.status = "released"
            row.settled_tokens = 0
            session.flush()
            session.refresh(row)
            session.expunge(row)
            return row

    def reap_expired(self, now: datetime | None = None) -> int:
        """Release all expired leases and return the number reaped."""

        current = self._now(now)
        count = 0
        with self._session() as session:
            rows = session.execute(
                select(LlmTokenReservation)
                .where(
                    LlmTokenReservation.status == "reserved",
                    LlmTokenReservation.lease_expires_at.is_not(None),
                    LlmTokenReservation.lease_expires_at <= current,
                )
                .with_for_update()
            ).scalars().all()
            for row in rows:
                ledger = session.execute(
                    select(LlmDailyBudget)
                    .where(LlmDailyBudget.budget_date == row.budget_date)
                    .with_for_update()
                ).scalar_one()
                ledger.reserved_tokens = max(0, int(ledger.reserved_tokens) - int(row.reserved_tokens))
                row.status = "expired"
                row.settled_tokens = 0
                count += 1
        return count


__all__ = [
    "BEIJING",
    "DEFAULT_DAILY_TOKEN_LIMIT",
    "DEFAULT_LEASE_SECONDS",
    "TokenBudgetService",
]

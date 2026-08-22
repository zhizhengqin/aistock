"""Idempotent post-market snapshot persistence and bounded retention."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.datahub import DataSnapshot, IngestionRun


class SnapshotStore:
    def __init__(self, db: Session) -> None:
        self.db = db

    def upsert(
        self,
        dataset: str,
        trade_date: str,
        scope_key: str,
        schema_version: str,
        source: str,
        payload: Any,
    ) -> DataSnapshot:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        values = {
            "dataset": dataset,
            "trade_date": trade_date,
            "scope_key": scope_key,
            "schema_version": schema_version,
            "source": source,
            "payload_json": payload,
            "payload_hash": digest,
            "fetched_at": now,
        }
        dialect = self.db.bind.dialect.name if self.db.bind is not None else ""
        insert = pg_insert(DataSnapshot) if dialect == "postgresql" else sqlite_insert(DataSnapshot)
        statement = insert.values(**values).on_conflict_do_update(
            index_elements=["dataset", "trade_date", "scope_key", "schema_version", "source"],
            set_={"payload_json": payload, "payload_hash": digest, "fetched_at": now},
        )
        self.db.execute(statement)
        self.db.commit()
        # ``expire_on_commit`` is intentionally disabled by the runtime session
        # factory.  The identity map can therefore still hold the row from the
        # previous upsert, even though PostgreSQL has already applied the
        # ``ON CONFLICT DO UPDATE``.  ``populate_existing`` makes the returned
        # object reflect the committed payload for every caller/session policy.
        return self.db.execute(
            select(DataSnapshot)
            .where(
                DataSnapshot.dataset == dataset,
                DataSnapshot.trade_date == trade_date,
                DataSnapshot.scope_key == scope_key,
                DataSnapshot.schema_version == schema_version,
                DataSnapshot.source == source,
            )
            .execution_options(populate_existing=True)
        ).scalar_one()

    def record_run(self, dataset: str, trade_date: str, *, status: str, counts: dict[str, Any] | None = None, payload_hash: str | None = None, error: str | None = None) -> IngestionRun:
        row = IngestionRun(dataset=dataset, trade_date=trade_date, status=status, counts_json=counts, payload_hash=payload_hash, error=error, started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc))
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def latest(
        self,
        dataset: str,
        scope_key: str,
        *,
        schema_version: str = "1.0",
        source: str = "datahub",
    ) -> DataSnapshot | None:
        """Return the newest snapshot for one complete identity."""

        return self.db.execute(
            select(DataSnapshot)
            .where(
                DataSnapshot.dataset == dataset,
                DataSnapshot.scope_key == scope_key,
                DataSnapshot.schema_version == schema_version,
                DataSnapshot.source == source,
            )
            .order_by(DataSnapshot.trade_date.desc(), DataSnapshot.fetched_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    def history(
        self,
        dataset: str,
        scope_key: str,
        *,
        limit: int = 6,
        schema_version: str = "1.0",
        source: str = "datahub",
    ) -> list[DataSnapshot]:
        """Return at most ``limit`` newest distinct trade dates."""

        bounded = max(1, min(int(limit), 3650))
        rows = self.db.execute(
            select(DataSnapshot)
            .where(
                DataSnapshot.dataset == dataset,
                DataSnapshot.scope_key == scope_key,
                DataSnapshot.schema_version == schema_version,
                DataSnapshot.source == source,
            )
            .order_by(DataSnapshot.trade_date.desc(), DataSnapshot.fetched_at.desc())
            .limit(bounded * 2)
        ).scalars().all()
        result: list[DataSnapshot] = []
        seen: set[str] = set()
        for row in rows:
            if row.trade_date in seen:
                continue
            seen.add(row.trade_date)
            result.append(row)
            if len(result) >= bounded:
                break
        return result

    def cleanup(self, *, retention_days: int = 730) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        result = self.db.execute(delete(DataSnapshot).where(DataSnapshot.fetched_at < cutoff))
        self.db.commit()
        return int(result.rowcount or 0)


__all__ = ["SnapshotStore"]

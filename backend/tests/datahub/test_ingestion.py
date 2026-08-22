from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.datahub.ingestion import SnapshotStore
from app.models.base import Base
from app.models.datahub import DataSnapshot


def test_snapshot_upsert_is_idempotent_and_updates_payload_hash():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    store = SnapshotStore(Session(engine))
    first = store.upsert("kpl.limit_list", "20260822", "all", "1.0", "tushare", [{"a": 1}])
    second = store.upsert("kpl.limit_list", "20260822", "all", "1.0", "tushare", [{"a": 2}])
    assert first.id == second.id
    assert store.db.query(DataSnapshot).count() == 1
    assert store.db.query(DataSnapshot).one().payload_json == [{"a": 2}]


def test_snapshot_retention_deletes_only_records_older_than_cutoff():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    store = SnapshotStore(db)
    old = store.upsert("kpl.limit_list", "20240822", "all", "1.0", "tushare", [{"a": 1}])
    old.fetched_at = datetime.now(timezone.utc) - timedelta(days=800)
    db.commit()
    store.upsert("kpl.limit_list", "20260822", "all", "1.0", "tushare", [{"a": 2}])
    removed = store.cleanup(retention_days=730)
    assert removed == 1
    assert db.query(DataSnapshot).count() == 1


def test_snapshot_latest_filters_identity_and_returns_newest_trade_date():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    store = SnapshotStore(Session(engine))
    store.upsert("market.hotspots.v1", "20260820", "industry", "1.0", "datahub", [{"day": 20}])
    store.upsert("market.hotspots.v1", "20260822", "industry", "1.0", "datahub", [{"day": 22}])
    store.upsert("market.hotspots.v1", "20260823", "theme", "1.0", "datahub", [{"day": 23}])
    store.upsert("market.hotspots.v1", "20260824", "industry", "1.0", "other", [{"day": 24}])

    latest = store.latest("market.hotspots.v1", "industry", schema_version="1.0", source="datahub")

    assert latest is not None
    assert latest.trade_date == "20260822"
    assert latest.payload_json == [{"day": 22}]


def test_snapshot_history_returns_six_distinct_days_without_scope_or_source_leakage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    store = SnapshotStore(Session(engine))
    for day in range(15, 23):
        store.upsert("market.hotspots.v1", f"202608{day:02d}", "industry", "1.0", "datahub", [{"day": day}])
    store.upsert("market.hotspots.v1", "20260824", "theme", "1.0", "datahub", [{"day": 24}])
    store.upsert("market.hotspots.v1", "20260825", "industry", "1.0", "other", [{"day": 25}])

    history = store.history("market.hotspots.v1", "industry", limit=6, schema_version="1.0", source="datahub")

    assert [item.trade_date for item in history] == ["20260822", "20260821", "20260820", "20260819", "20260818", "20260817"]

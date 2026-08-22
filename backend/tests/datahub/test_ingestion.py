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

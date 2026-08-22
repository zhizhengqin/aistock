from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.datahub import (
    DataSourceAuditEvent,
    DataSourceConfig,
    DataSourceProbeRun,
    DataSourceRoute,
    DataSnapshot,
    IngestionRun,
)


def test_datahub_models_create_configuration_probe_audit_and_snapshot_tables():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())
    assert {
        "data_source_configs",
        "data_source_routes",
        "data_source_probe_runs",
        "data_source_audit_events",
        "data_ingestion_runs",
        "data_snapshots",
    } <= tables


def test_snapshot_identity_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        snapshot = DataSnapshot(
            dataset="kpl.limit_list",
            trade_date="20260822",
            scope_key="all",
            schema_version="1.0",
            source="tushare",
            payload_json=[{"ts_code": "000001.SZ"}],
            payload_hash="hash-1",
        )
        session.add(snapshot)
        session.commit()
        assert session.query(DataSnapshot).count() == 1
        assert DataSnapshot.__table__.constraints

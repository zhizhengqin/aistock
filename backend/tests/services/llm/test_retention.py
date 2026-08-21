from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.models.llm_config import LlmAdminAuditEvent, LlmModelConfig
from app.models.llm_execution import LlmCallAttempt, LlmTokenReservation
from app.models.llm_usage import LlmUsage
from app.models.task_outbox import TaskOutbox
from app.models.task_record import TaskRecord
from app.services.llm.config_service import LlmConfigService
from app.services.llm.retention import cleanup_llm_audit_payloads


UTC = timezone.utc
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _task(db, *, status: str = "success", task_id: str | None = None, lease: bool = False):
    task = TaskRecord(
        id=task_id,
        task_type="retention-test",
        status=status,
        input_snapshot={"symbol": "000001"},
        execution_token="live-token" if lease else None,
        lease_expires_at=(NOW + timedelta(hours=1)) if lease else None,
    )
    db.add(task)
    db.flush()
    return task


def _attempt(
    db,
    *,
    task: TaskRecord | None,
    age_days: int = 91,
    status: str = "success",
    ordinal: int = 1,
):
    attempt = LlmCallAttempt(
        task_id=task.id if task else None,
        model_config_id=None,
        operation_type="task",
        step_key=f"retention.test.{ordinal}.v1",
        attempt_no=ordinal,
        provider_snapshot="deepseek",
        model_snapshot="deepseek-chat",
        runtime_fingerprint="fingerprint",
        prompt_version="v1",
        result_json={"summary": "private"},
        response_metadata_json={"raw": "private"},
        result_hash="result-hash",
        result_schema_version="v1",
        status=status,
        input_tokens=10,
        output_tokens=20,
        cost_micro_yuan=30,
        error_code="E_TEST" if status != "success" else None,
        error_message="测试错误" if status != "success" else None,
        created_at=NOW - timedelta(days=age_days),
    )
    db.add(attempt)
    db.flush()
    return attempt


def test_cleanup_obeys_terminal_and_safety_guards(test_db):
    _, session_factory = test_db
    db = session_factory()
    config = LlmModelConfig(
        id="cfg-retention",
        provider="deepseek",
        display_name="Retention test config",
        model_name="deepseek-chat",
        base_url="https://example.test/v1",
        encrypted_api_key="ciphertext-only",
        encryption_key_id="test-key-id",
        envelope_version="v1",
        nonce="nonce",
        runtime_fingerprint="retention-fingerprint",
    )
    db.add(config)
    db.flush()
    audit = LlmAdminAuditEvent(
        model_config_id=config.id,
        event_type="retention-test",
        reason="保留审计测试",
        payload_json={"keep": "forever"},
    )
    db.add(audit)
    eligible = []
    for ordinal, status in enumerate(("success", "failed", "failed_unknown"), start=1):
        task = _task(db, status=status)
        task.result_json = {"final": "keep"}
        eligible.append(_attempt(db, task=task, status=status, ordinal=ordinal))
    recent = _attempt(db, task=_task(db), age_days=89)
    boundary = _attempt(db, task=_task(db), age_days=90, ordinal=10)
    running = _attempt(db, task=_task(db, status="running"), ordinal=11)
    pending_task = _task(db, status="pending")
    pending = _attempt(db, task=pending_task, ordinal=12)
    db.add(TaskOutbox(task_id=pending.task_id, status="pending", available_at=NOW))
    recoverable = _attempt(db, task=_task(db, status="recoverable"), ordinal=13)
    locked = _attempt(db, task=_task(db), ordinal=14)
    db.add(TaskOutbox(task_id=locked.task_id, status="locked", available_at=NOW, locked_at=NOW))
    reserved = _attempt(db, task=_task(db), ordinal=15)
    db.add(
        LlmTokenReservation(
            task_id=reserved.task_id,
            step_key="retention.test.v1",
            budget_date=NOW.date(),
            status="reserved",
            reserved_tokens=1,
        )
    )
    leased = _attempt(db, task=_task(db, lease=True), ordinal=16)
    no_task = _attempt(db, task=None, ordinal=17)
    db.commit()

    result = cleanup_llm_audit_payloads(session_factory, clock=lambda: NOW)
    assert result["affected_rows"] == 3
    assert result["batches"] == 1
    assert result["cutoff"] == NOW - timedelta(days=90)

    db.expire_all()
    for attempt in eligible:
        refreshed = db.get(LlmCallAttempt, attempt.id)
        assert refreshed.result_json is None
        assert refreshed.response_metadata_json is None
        assert refreshed.result_hash == "result-hash"
        assert refreshed.result_schema_version == "v1"
        assert refreshed.status in {"success", "failed", "failed_unknown"}
        assert refreshed.provider_snapshot == "deepseek"
        assert refreshed.model_snapshot == "deepseek-chat"
        assert refreshed.input_tokens == 10
        assert refreshed.output_tokens == 20
        assert refreshed.cost_micro_yuan == 30
        raw = db.execute(
            text(
                "SELECT result_json, response_metadata_json "
                "FROM llm_call_attempts WHERE id = :id"
            ),
            {"id": attempt.id},
        ).one()
        assert raw.result_json is None
        assert raw.response_metadata_json is None
    for attempt in (recent, boundary, running, pending, recoverable, locked, reserved, leased, no_task):
        refreshed = db.get(LlmCallAttempt, attempt.id)
        assert refreshed.result_json is not None
        assert refreshed.response_metadata_json is not None
    for attempt in eligible:
        assert attempt.error_code is None or attempt.error_code == "E_TEST"
        refreshed = db.get(LlmCallAttempt, attempt.id)
        assert refreshed.error_code == attempt.error_code
        assert refreshed.error_message == attempt.error_message
        assert db.get(TaskRecord, attempt.task_id).result_json == {"final": "keep"}
    assert db.get(LlmModelConfig, config.id).encrypted_api_key == "ciphertext-only"
    assert db.get(LlmAdminAuditEvent, audit.id).payload_json == {"keep": "forever"}

    second = cleanup_llm_audit_payloads(session_factory, clock=lambda: NOW)
    assert second["affected_rows"] == 0
    assert second["batches"] == 0


def test_cleanup_processes_at_most_500_rows_per_batch(test_db):
    _, session_factory = test_db
    db = session_factory()
    task = _task(db)
    for ordinal in range(1, 502):
        _attempt(db, task=task, ordinal=ordinal)
    db.commit()

    result = cleanup_llm_audit_payloads(session_factory, clock=lambda: NOW)
    assert result["affected_rows"] == 501
    assert result["batches"] == 2


def test_usage_groups_by_beijing_date_and_marks_unknown_cost(test_db):
    _, session_factory = test_db
    db = session_factory()
    db.add_all(
        [
            LlmUsage(
                module="analysis",
                model="deepseek-chat",
                provider_snapshot="deepseek",
                model_snapshot="deepseek-chat",
                model_config_id="cfg-a",
                input_tokens=10,
                output_tokens=20,
                cost_micro_yuan=30,
                input_price_snapshot=1,
                output_price_snapshot=1,
                status="success",
                created_at=datetime(2026, 8, 20, 16, 30, tzinfo=UTC),
            ),
            LlmUsage(
                module="analysis",
                model="deepseek-chat",
                provider_snapshot="deepseek",
                model_snapshot="deepseek-chat",
                model_config_id="cfg-a",
                input_tokens=1,
                output_tokens=2,
                cost_micro_yuan=None,
                input_price_snapshot=None,
                output_price_snapshot=1,
                status="success",
                created_at=datetime(2026, 8, 20, 17, 30, tzinfo=UTC),
            ),
            LlmUsage(
                module="news",
                model="kimi",
                provider_snapshot="kimi",
                model_snapshot="kimi",
                model_config_id="cfg-b",
                input_tokens=5,
                output_tokens=6,
                cost_micro_yuan=11,
                input_price_snapshot=1,
                output_price_snapshot=1,
                status="success",
                created_at=datetime(2026, 8, 21, 0, 30, tzinfo=UTC),
            ),
        ]
    )
    db.commit()

    service = LlmConfigService(db, clock=lambda: NOW)
    payload = service.usage(days=7)
    assert payload["total_calls"] == 3
    assert payload["total_cost_micro_yuan"] is None
    assert {item["date"] for item in payload["items"]} == {"2026-08-21"}
    deepseek = next(item for item in payload["items"] if item["provider"] == "deepseek")
    assert deepseek["model_config_id"] == "cfg-a"
    assert deepseek["cost_micro_yuan"] is None
    kimi = next(item for item in payload["items"] if item["provider"] == "kimi")
    assert kimi["cost_micro_yuan"] == 11


def test_scheduler_registers_daily_retention_job(monkeypatch):
    from app.core.config import settings
    from app.tasks import scheduler as scheduler_module

    class FakeScheduler:
        def __init__(self):
            self.running = False
            self.jobs = []

        def add_job(self, *args, **kwargs):
            self.jobs.append((args, kwargs))

        def start(self):
            self.running = True

    fake = FakeScheduler()
    monkeypatch.setattr(scheduler_module, "get_scheduler", lambda: fake)
    monkeypatch.setattr(settings, "TASK_INLINE", False)
    scheduler_module.start_scheduler(force=True)
    assert any(kwargs.get("id") == "llm_audit_retention_daily" for _, kwargs in fake.jobs)

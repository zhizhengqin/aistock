"""PostgreSQL concurrency evidence for task submission and outbox claiming."""

from __future__ import annotations

import asyncio
import base64
import importlib
import os
import pkgutil
import re
import threading
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base
from app.models.llm_config import LlmModelConfig, LlmModelTestRun, LlmRuntimeSetting
from app.models.task_outbox import TaskOutbox
from app.models.task_record import TaskRecord
from app.models.user import User
from app.models.usage_log import UsageLog
from app.core.config import settings
from app.services.llm.config_service import LlmConfigService
from app.services.llm.crypto import encrypt_api_key
from app.services.llm.provider_client import ProviderResult
from app.services.llm.types import Provider
from app.services.task_submission import TaskSubmission, TaskSubmissionService


_DATABASE_PATTERN = re.compile(r"^aistock_task_submission_test_[0-9a-f]{12}$")


def _import_all_models():
    import app.models

    for module in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module(f"app.models.{module.name}")


@contextmanager
def _temporary_database(database_url: str):
    base_url = make_url(database_url)
    if base_url.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL 必须使用 PostgreSQL")
    admin_engine = create_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    database_name = f"aistock_task_submission_test_{uuid.uuid4().hex[:12]}"
    assert _DATABASE_PATTERN.fullmatch(database_name)
    engine = None
    created = False
    try:
        with admin_engine.connect() as admin:
            admin.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        engine = create_engine(base_url.set(database=database_name), pool_size=40, max_overflow=40)
        _import_all_models()
        Base.metadata.create_all(engine)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin_engine.connect() as admin:
                admin.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :name AND pid <> pg_backend_pid()"
                    ),
                    {"name": database_name},
                )
                admin.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()


def _default_model(db: Session, *, name: str, fingerprint: str) -> LlmModelConfig:
    from app.services.llm.config_service import runtime_fingerprint

    config = LlmModelConfig(
        provider="deepseek",
        display_name=name,
        model_name="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        encrypted_api_key="ciphertext",
        encryption_key_id="test",
        envelope_version="v1",
        nonce="nonce",
        runtime_fingerprint="pending",
        lifecycle_status="active",
    )
    config.runtime_fingerprint = runtime_fingerprint(
        provider=config.provider,
        model_name=config.model_name,
        base_url=config.base_url,
        credential_version=config.credential_version,
        max_output_tokens=config.max_output_tokens or 4096,
    )
    db.add(config)
    db.flush()
    run = LlmModelTestRun(
        model_config_id=config.id,
        runtime_fingerprint=config.runtime_fingerprint,
        status="success",
        test_type="probe",
    )
    db.add(run)
    db.flush()
    config.verified_test_id = run.id
    return config


def _seed(factory, *, tier: str = "A", two_models: bool = False):
    from app.services.membership import ensure_plans

    with factory() as db:
        user = User(
            username=f"task-race-{uuid.uuid4().hex[:8]}",
            email=f"task-race-{uuid.uuid4().hex[:8]}@example.test",
            password_hash="test-only",
            tier=tier,
            role="user",
            is_active=True,
        )
        db.add(user)
        db.flush()
        ensure_plans(db)
        first = _default_model(db, name="旧默认", fingerprint="fingerprint-old")
        second = _default_model(db, name="新默认", fingerprint="fingerprint-new") if two_models else None
        db.add(LlmRuntimeSetting(default_model_config_id=first.id))
        db.commit()
        return user.id, first.id, second.id if second else None


def _submission(user_id: int, *, cost: int = 1) -> TaskSubmission:
    return TaskSubmission(
        task_type="stock_analysis",
        user_id=user_id,
        feature="stock_analysis",
        feature_cost=cost,
        args={"stock_code": "600519", "user_id": user_id},
        input_snapshot={"stock_code": "600519"},
        prompt_version="pg-race-v1",
    )


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="需要显式 TEST_DATABASE_URL 指向 disposable PostgreSQL",
)


def test_postgres_activation_and_submission_are_linearized(monkeypatch):
    with _temporary_database(os.environ["TEST_DATABASE_URL"]) as engine:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        user_id, old_id, new_id = _seed(factory, two_models=True)
        keyring = {"task6-test": base64.b64encode(b"t" * 32).decode("ascii")}
        monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEY_ID", "task6-test")
        monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEYS", keyring)
        with factory() as db:
            target = db.get(LlmModelConfig, new_id)
            envelope = encrypt_api_key(
                "sk-task6-test",
                config_id=target.id,
                provider=Provider.DEEPSEEK,
                keyring=keyring,
            )
            target.encrypted_api_key = envelope.encrypted_api_key
            target.encryption_key_id = envelope.encryption_key_id
            target.envelope_version = envelope.envelope_version
            target.nonce = envelope.nonce
            db.commit()

        class ProbeGateExecutor:
            def __init__(self):
                self.probe_started = threading.Event()
                self.release_probe = threading.Event()

            async def call(self, **kwargs):
                self.probe_started.set()
                while not self.release_probe.is_set():
                    await asyncio.sleep(0.005)
                return ProviderResult(
                    result_json={"decision": "hold", "confidence": 0.5, "rationale": "race probe"},
                    model="deepseek-chat",
                    prompt_tokens=8,
                    completion_tokens=4,
                    usage_source="provider",
                    response_metadata={"provider_model_present": True},
                )

        executor = ProbeGateExecutor()
        final_lock_barrier = threading.Barrier(2)
        lock_order: list[str] = []
        order_lock = threading.Lock()
        submission_ready = threading.Event()
        original_activation_lock = LlmConfigService._locked_settings
        original_submission_lock = TaskSubmissionService._locked_settings

        def activation_lock(self, session, *, create=True):
            final_lock_barrier.wait(timeout=10)
            setting = original_activation_lock(self, session, create=create)
            with order_lock:
                lock_order.append("activation")
            return setting

        def submission_lock(self):
            submission_ready.set()
            final_lock_barrier.wait(timeout=10)
            setting = original_submission_lock(self)
            with order_lock:
                lock_order.append("submission")
            return setting

        monkeypatch.setattr(LlmConfigService, "_locked_settings", activation_lock)
        monkeypatch.setattr(TaskSubmissionService, "_locked_settings", submission_lock)

        def submit():
            with factory() as db:
                result = TaskSubmissionService(db).submit(_submission(user_id))
                return result.task_ids[0], result.task.model_config_id

        def activate():
            with factory() as db:
                return asyncio.run(
                    LlmConfigService(db, executor=executor).activate(
                        new_id,
                        expected_version=1,
                        idempotency_key=f"task6-race-{uuid.uuid4().hex}",
                        admin_user_id=1,
                    )
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            activate_future = pool.submit(activate)
            assert executor.probe_started.wait(timeout=10)
            submit_future = pool.submit(submit)
            assert submission_ready.wait(timeout=10)
            executor.release_probe.set()
            submitted_id, model_id = submit_future.result(timeout=20)
            activation_result = activate_future.result(timeout=20)

        assert activation_result["id"] == new_id
        assert lock_order and lock_order[0] in {"activation", "submission"}
        expected_model = old_id if lock_order[0] == "submission" else new_id
        assert model_id == expected_model
        with Session(engine) as db:
            task = db.get(TaskRecord, submitted_id)
            assert task is not None
            assert task.model_config_id == expected_model
            assert db.query(TaskOutbox).filter(TaskOutbox.task_id == submitted_id).count() == 1


def test_postgres_twenty_submissions_never_overconsume_quota():
    with _temporary_database(os.environ["TEST_DATABASE_URL"]) as engine:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        user_id, _, _ = _seed(factory, tier="C")
        barrier = threading.Barrier(20)

        def submit_one(_index):
            with factory() as db:
                barrier.wait(timeout=10)
                try:
                    return TaskSubmissionService(db).submit(_submission(user_id))
                except Exception as exc:
                    return exc

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(submit_one, range(20)))

        successes = [item for item in results if not isinstance(item, Exception)]
        failures = [item for item in results if isinstance(item, Exception)]
        assert len(successes) == 8
        assert len(failures) == 12
        assert all(
            getattr(item, "detail", {}).get("code") == "quota_exceeded"
            for item in failures
        )
        with Session(engine) as db:
            usage = db.query(UsageLog).filter(UsageLog.user_id == user_id).one()
            assert usage.count == 8
            assert db.query(TaskRecord).filter(TaskRecord.user_id == user_id).count() == 8
            assert db.query(TaskOutbox).join(TaskRecord, TaskOutbox.task_id == TaskRecord.id).filter(TaskRecord.user_id == user_id).count() == 8


class _CountingSender:
    def __init__(self):
        self.jobs: list[tuple[str, tuple, str]] = []
        self._lock = threading.Lock()
        self.barrier = threading.Barrier(2)

    async def enqueue_job(self, name, *args, _job_id=None, **kwargs):
        self.barrier.wait(timeout=10)
        with self._lock:
            self.jobs.append((name, args, _job_id))
        return None


def test_postgres_two_dispatchers_emit_one_logical_job_each():
    with _temporary_database(os.environ["TEST_DATABASE_URL"]) as engine:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        user_id, _, _ = _seed(factory)
        with factory() as db:
            for index in range(10):
                task = TaskRecord(
                    task_type="stock_analysis",
                    user_id=user_id,
                    status="pending",
                    input_snapshot={
                        "_args": {"stock_code": f"6005{index:02d}", "user_id": user_id}
                    },
                )
                db.add(task)
                db.flush()
                db.add(TaskOutbox(task_id=task.id))
            db.commit()

        from app.services.outbox_dispatcher import OutboxDispatcher

        sender = _CountingSender()
        first = OutboxDispatcher(factory, sender=sender, batch_size=5, worker_id="worker-a")
        second = OutboxDispatcher(factory, sender=sender, batch_size=5, worker_id="worker-b")

        def dispatch(dispatcher):
            return asyncio.run(dispatcher.dispatch_once())

        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(dispatch, first)
            second_future = pool.submit(dispatch, second)
            counts = [first_future.result(timeout=20), second_future.result(timeout=20)]

        assert sorted(counts) == [5, 5]
        assert len(sender.jobs) == 10
        assert len({job[2] for job in sender.jobs}) == 10
        with Session(engine) as db:
            assert db.query(TaskOutbox).filter(TaskOutbox.status == "delivered").count() == 10

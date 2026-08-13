"""PostgreSQL concurrency evidence for Task 4 configuration workflows."""

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
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.base import Base
from app.models.llm_config import LlmModelConfig, LlmRuntimeSetting
from app.models.user import User
from app.services.llm.config_service import LlmConfigService
from app.services.llm.provider_client import ProviderResult
from app.services.llm.types import ModelLifecycle


_DB_NAME_PATTERN = re.compile(r"^aistock_llm_config_test_[0-9a-f]{12}$")


def _import_all_models():
    import app.models

    for module in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module(f"app.models.{module.name}")


@contextmanager
def _temporary_database(database_url: str):
    base_url = make_url(database_url)
    if base_url.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL 必须使用 PostgreSQL")
    admin_url = base_url.set(database="postgres")
    database_name = f"aistock_llm_config_test_{uuid.uuid4().hex[:12]}"
    assert _DB_NAME_PATTERN.fullmatch(database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    test_engine = None
    created = False
    try:
        with admin_engine.connect() as admin:
            admin.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        test_engine = create_engine(base_url.set(database=database_name), pool_size=30, max_overflow=30)
        _import_all_models()
        Base.metadata.create_all(test_engine)
        yield test_engine, database_name
    finally:
        if test_engine is not None:
            test_engine.dispose()
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


class CountingExecutor:
    def __init__(self, *, delay: float = 0.05):
        self.calls = 0
        self._lock = threading.Lock()
        self._barrier = None
        self.delay = delay

    def barrier(self, parties: int):
        self._barrier = threading.Barrier(parties)

    async def call(self, **kwargs):
        with self._lock:
            self.calls += 1
        if self._barrier is not None:
            try:
                self._barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass
        await asyncio.sleep(self.delay)
        return ProviderResult(
            result_json={"decision": "hold", "confidence": 0.5, "rationale": "probe"},
            model="deepseek-chat",
            prompt_tokens=8,
            completion_tokens=4,
            usage_source="provider",
            response_metadata={"provider_model_present": True},
        )


def _configure_keyring(monkeypatch):
    monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEY_ID", "pg-test-current")
    monkeypatch.setattr(
        settings,
        "LLM_CONFIG_ENCRYPTION_KEYS",
        {"pg-test-current": base64.b64encode(b"p" * 32).decode("ascii")},
    )


def _candidate(name: str = "PG model"):
    return {
        "provider": "deepseek",
        "display_name": name,
        "model_name": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-pg-secret",
        "max_output_tokens": 256,
    }


def _seed_admins(factory, count: int = 20):
    """Satisfy audit/config user foreign keys in the disposable database."""

    with factory() as db:
        db.add_all(
            [
                User(
                    id=index,
                    username=f"llm-concurrency-{index}",
                    email=f"llm-concurrency-{index}@example.test",
                    password_hash="test-only",
                    role="admin",
                )
                for index in range(1, count + 1)
            ]
        )
        db.commit()


def _create_config(factory, executor, name="PG model"):
    with factory() as db:
        return LlmConfigService(db, executor=executor).create(_candidate(name), admin_user_id=1)


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要显式 TEST_DATABASE_URL")
def test_postgres_patch_same_expected_version_has_exactly_one_winner(monkeypatch):
    _configure_keyring(monkeypatch)
    with _temporary_database(os.environ["TEST_DATABASE_URL"]) as (engine, _):
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        _seed_admins(factory)
        executor = CountingExecutor()
        created = _create_config(factory, executor)
        barrier = threading.Barrier(20)

        def one_patch(index):
            with factory() as db:
                barrier.wait(timeout=10)
                try:
                    return LlmConfigService(db, executor=executor).patch(
                        created["id"],
                        {"expected_version": 1, "display_name": f"winner-{index}"},
                        admin_user_id=index,
                    )
                except Exception as exc:
                    return exc

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(one_patch, range(20)))
        successes = [item for item in results if isinstance(item, dict)]
        failures = [item for item in results if not isinstance(item, dict)]
        assert len(successes) == 1
        assert len(failures) == 19
        assert all(getattr(item, "code", None) == "llm_config_conflict" for item in failures)

        with Session(engine) as db:
            config = db.get(LlmModelConfig, created["id"])
            assert config.version == 2


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要显式 TEST_DATABASE_URL")
def test_postgres_activation_serializes_default_against_disable_and_delete(monkeypatch):
    _configure_keyring(monkeypatch)
    with _temporary_database(os.environ["TEST_DATABASE_URL"]) as (engine, _):
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        _seed_admins(factory)
        overlap = threading.Barrier(3)

        class OverlapExecutor(CountingExecutor):
            async def call(self, **kwargs):
                with self._lock:
                    self.calls += 1
                await asyncio.to_thread(overlap.wait, 10)
                await asyncio.sleep(self.delay)
                return ProviderResult(
                    result_json={"decision": "hold", "confidence": 0.5, "rationale": "probe"},
                    model="deepseek-chat",
                    prompt_tokens=8,
                    completion_tokens=4,
                    usage_source="provider",
                    response_metadata={"provider_model_present": True},
                )

        executor = OverlapExecutor()
        first = _create_config(factory, executor, "first")
        second = _create_config(factory, executor, "second")
        with factory() as db:
            first_row = db.get(LlmModelConfig, first["id"])
            first_row.lifecycle_status = ModelLifecycle.ACTIVE.value
            setting = db.get(LlmRuntimeSetting, 1)
            setting.default_model_config_id = first["id"]
            db.commit()

        # The target starts active but is not default.  Activation racing with
        # disable/delete must leave whichever committed default active and not
        # soft-deleted.
        with factory() as db:
            second_row = db.get(LlmModelConfig, second["id"])
            second_row.lifecycle_status = ModelLifecycle.ACTIVE.value
            db.commit()

        async def activate():
            with factory() as db:
                return await LlmConfigService(db, executor=executor).activate(
                    second["id"], expected_version=1, idempotency_key="race-default", admin_user_id=1
                )

        def disable():
            with factory() as db:
                try:
                    overlap.wait(timeout=10)
                    return LlmConfigService(db, executor=executor).disable(second["id"], expected_version=1, admin_user_id=2)
                except Exception as exc:
                    return exc

        def delete():
            with factory() as db:
                try:
                    overlap.wait(timeout=10)
                    return LlmConfigService(db, executor=executor).delete(second["id"], admin_user_id=3)
                except Exception as exc:
                    return exc

        async def race():
            activation = asyncio.create_task(activate())
            await asyncio.sleep(0)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(disable), pool.submit(delete)]
                results = [future.result() for future in futures]
            try:
                activation_result = await activation
            except Exception as exc:
                activation_result = exc
            return activation_result, results

        activation_result, operation_results = asyncio.run(race())
        allowed_activation_errors = {
            "llm_config_conflict",
            "llm_config_not_found",
            "llm_activation_owner_lost",
            "llm_probe_failed",
        }
        allowed_disable_errors = {
            "llm_default_disable_forbidden",
            "llm_config_conflict",
            "llm_config_not_found",
            "llm_invalid_state_transition",
        }
        allowed_delete_errors = {
            "llm_default_delete_forbidden",
            "llm_active_delete_forbidden",
            "llm_config_not_found",
            "llm_invalid_state_transition",
        }
        if isinstance(activation_result, Exception):
            assert getattr(activation_result, "code", None) in allowed_activation_errors
        else:
            assert activation_result["id"] == second["id"]
            assert activation_result["lifecycle_status"] == ModelLifecycle.ACTIVE.value
        disable_result, delete_result = operation_results
        if isinstance(disable_result, Exception):
            assert getattr(disable_result, "code", None) in allowed_disable_errors
        else:
            assert disable_result["lifecycle_status"] == ModelLifecycle.DISABLED.value
        if isinstance(delete_result, Exception):
            assert getattr(delete_result, "code", None) in allowed_delete_errors
        else:
            assert delete_result is None
        with Session(engine) as db:
            setting = db.get(LlmRuntimeSetting, 1)
            default = db.get(LlmModelConfig, setting.default_model_config_id)
            assert default is not None
            assert default.lifecycle_status == ModelLifecycle.ACTIVE.value
            assert default.deleted_at is None
            rows = {row.id: row for row in db.query(LlmModelConfig).all()}
            assert rows[setting.default_model_config_id].lifecycle_status == ModelLifecycle.ACTIVE.value
            assert rows[setting.default_model_config_id].deleted_at is None
            if setting.default_model_config_id == first["id"]:
                assert rows[second["id"]].lifecycle_status in {
                    ModelLifecycle.DISABLED.value,
                    ModelLifecycle.RETIRED.value,
                }
            else:
                assert setting.default_model_config_id == second["id"]


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要显式 TEST_DATABASE_URL")
def test_postgres_activation_idempotency_has_one_probe_and_same_response(monkeypatch):
    _configure_keyring(monkeypatch)
    with _temporary_database(os.environ["TEST_DATABASE_URL"]) as (engine, _):
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        _seed_admins(factory)
        executor = CountingExecutor(delay=0.1)
        created = _create_config(factory, executor, "idempotent")

        async def one_call(admin_id):
            with factory() as db:
                return await LlmConfigService(db, executor=executor).activate(
                    created["id"], expected_version=1, idempotency_key="same-key", admin_user_id=admin_id
                )

        async def run_pair():
            return await asyncio.gather(one_call(1), one_call(2))

        results = asyncio.run(run_pair())
        assert results[0] == results[1]
        assert executor.calls == 1

        with factory() as db:
            service = LlmConfigService(db, executor=executor)
            with pytest.raises(Exception) as exc:
                asyncio.run(
                    service.activate(
                        created["id"], expected_version=2, idempotency_key="same-key", admin_user_id=3
                    )
                )
            assert getattr(exc.value, "code", None) == "llm_idempotency_conflict"
            assert executor.calls == 1

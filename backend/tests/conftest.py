import base64

import pytest
import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.models.base import Base
from app.core import database as db_module
from app.core import redis as redis_module
from app.core.deps import get_current_user
from app.models.user import User


@pytest.fixture(scope="function")
def test_db():
   """Per-test in-memory SQLite with fresh schema."""
   engine = create_engine(
       "sqlite://",
       connect_args={"check_same_thread": False},
poolclass=StaticPool,
   )
   Base.metadata.create_all(bind=engine)
   TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
   yield engine, TestingSession
   Base.metadata.drop_all(bind=engine)


@pytest.fixture
def verified_llm_config(test_db, monkeypatch):
   """Create one encrypted, verified model fixture for hermetic unit tests."""
   from app.core.config import settings
   from app.models.llm_config import LlmModelConfig, LlmModelTestRun, LlmRuntimeSetting
   from app.services.llm.crypto import encrypt_api_key
   from app.services.llm.types import ModelLifecycle, Provider

   keyring = {"unit-test-current": base64.b64encode(b"u" * 32).decode("ascii")}
   monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEY_ID", "unit-test-current")
   monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEYS", keyring)
   engine, TestingSession = test_db
   db = TestingSession()
   config_id = "cfg-unit-verified"
   envelope = encrypt_api_key(
       "sk-unit-test-only",
       config_id=config_id,
       provider=Provider.DEEPSEEK,
       keyring=keyring,
   )
   config = LlmModelConfig(
       id=config_id,
       provider=Provider.DEEPSEEK.value,
       display_name="Unit verified model",
       model_name="deepseek-chat",
       base_url="https://api.deepseek.com/v1",
       encrypted_api_key=envelope.encrypted_api_key,
       encryption_key_id=envelope.encryption_key_id,
       envelope_version=envelope.envelope_version,
       nonce=envelope.nonce,
       runtime_fingerprint="unit-fingerprint",
       lifecycle_status=ModelLifecycle.ACTIVE.value,
   )
   run = LlmModelTestRun(
       model_config_id=config_id,
       runtime_fingerprint=config.runtime_fingerprint,
       status="success",
       result_json={"json": True},
   )
   config.verified_test_id = run.id
   db.add(config)
   db.add(run)
   db.add(LlmRuntimeSetting(default_model_config_id=config_id))
   db.commit()
   db.close()
   return {"id": config_id, "api_key": "sk-unit-test-only", "runtime_fingerprint": config.runtime_fingerprint}


@pytest.fixture(scope="function")
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_module, "redis_client", fake)
    from app.services import verify_code as vc_module
    monkeypatch.setattr(vc_module, "redis_client", fake)
    from app.datasource import cache as cache_module
    monkeypatch.setattr(cache_module, "redis_client", fake)
    return fake


@pytest.fixture(scope="function")
def client(test_db, fake_redis):
   engine, TestingSession = test_db
   def override_get_db():
       db = TestingSession()
       try:
           yield db
       finally:
           db.close()
   app.dependency_overrides[db_module.get_db] = override_get_db
   yield TestClient(app)
   app.dependency_overrides.clear()


@pytest.fixture
def api_prefix():
   return "/api"


@pytest.fixture
def seed_user(test_db, fake_redis):
   """Create one active user and return (user_obj, raw_password)."""
   from app.core.security import hash_password
   engine, TestingSession = test_db
   db = TestingSession()
   user = User(
       username="tester",
       email="tester@example.com",
       password_hash=hash_password("Passw0rd!"),
       role="user",
       tier="free",
       is_active=True,
   )
   db.add(user)
   db.commit()
   db.refresh(user)
   user_id = user.id
   db.close()
   return {"id": user_id, "username": "tester", "email": "tester@example.com", "password": "Passw0rd!"}


@pytest.fixture
def auth_client(client, seed_user):
   """Client with Authorization header pre-set for the seeded user."""
   from app.core.security import create_access_token
   token = create_access_token(seed_user["id"])
   client.headers.update({"Authorization": f"Bearer {token}"})
   return client

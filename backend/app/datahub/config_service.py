"""PostgreSQL-backed DataHub configuration and probe service.

The service accepts a SQLAlchemy session so API, worker and integration tests
share the same transaction semantics.  Redis is intentionally not consulted
for writes; callers may invalidate a route hint after commit.
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.datahub.contracts import Capability
from app.datahub.credentials import CredentialCipher, CredentialEnvelope, credential_fingerprint, key_hint
from app.datahub.errors import DataHubConflict, DataHubError, DataHubErrorCode
from app.datahub.registry import PROVIDER_REGISTRY, get_provider
from app.models.datahub import DataSourceAuditEvent, DataSourceConfig, DataSourceProbeRun, DataSourceRoute


@dataclass(frozen=True)
class ProbeRecord:
    provider: str
    capability: str
    status: str
    rows: int = 0
    latency_ms: int = 0
    error_code: str | None = None
    safe_sample: dict[str, Any] | None = None
    contract_version: str = "1.0"
    fingerprint: str | None = None


@dataclass(frozen=True)
class DataSourceView:
    id: str | None
    provider: str
    display_name: str
    description: str
    capabilities: list[str]
    auth_type: str
    credential_fields: list[dict[str, Any]]
    fee_type: str
    update_frequency: str
    risk_note: str
    available: bool
    unavailable_reason: str | None
    enabled: bool
    version: int
    key_hint: str | None
    fingerprint: str | None
    last_probe_status: str | None
    last_probe_at: datetime | None
    last_probe_latency_ms: int | None

    def model_dump(self) -> dict[str, Any]:
        return self.__dict__.copy()


class DataHubConfigService:
    PROBE_TTL = timedelta(minutes=15)
    config_model = DataSourceConfig

    def __init__(self, db: Session, *, encryption_key: bytes, encryption_key_id: str = "datahub-current") -> None:
        self.db = db
        self.cipher = CredentialCipher.from_key(encryption_key, key_id=encryption_key_id)

    def _credential_values(self, credentials: dict[str, str]) -> dict[str, str]:
        return {str(key): str(value) for key, value in credentials.items() if value not in (None, "")}

    def _validate_credential_keys(self, provider: str, credentials: dict[str, str]) -> None:
        definition = get_provider(provider)
        allowed = {field.key for field in definition.credential_fields}
        unknown = sorted(set(credentials) - allowed)
        if unknown:
            raise DataHubError(DataHubErrorCode.VALIDATION, "凭据字段不受该数据源支持")

    def _validate_required_credentials(self, provider: str, credentials: dict[str, str]) -> None:
        definition = get_provider(provider)
        missing = [
            field.label
            for field in definition.credential_fields
            if field.required and not credentials.get(field.key)
        ]
        if missing:
            raise DataHubError(DataHubErrorCode.VALIDATION, f"请完整填写必填凭据：{'、'.join(missing)}")

    def _decrypt_row_credentials(self, row: DataSourceConfig | None) -> dict[str, str]:
        if row is None or not row.encrypted_credentials:
            return {}
        try:
            payload = json.loads(row.encrypted_credentials)
            envelope = CredentialEnvelope(**payload)
            plaintext = self.cipher.decrypt(envelope, aad=b"datahub:credentials:v1")
            value = json.loads(plaintext)
            return value if isinstance(value, dict) else {}
        except Exception:
            raise DataHubError(DataHubErrorCode.AUTHENTICATION_FAILED, "数据源凭据无法解密") from None

    def _prepare_credentials(
        self,
        provider: str,
        credentials: dict[str, str],
        row: DataSourceConfig | None,
    ) -> dict[str, str]:
        self._validate_credential_keys(provider, credentials)
        merged = self._decrypt_row_credentials(row)
        merged.update(self._credential_values(credentials))
        self._validate_credential_keys(provider, merged)
        self._validate_required_credentials(provider, merged)
        return merged

    def merge_credentials(self, provider: str, credentials: dict[str, str]) -> dict[str, str]:
        """Merge a temporary probe's non-empty fields with saved credentials."""

        get_provider(provider)
        row = self.db.scalar(select(DataSourceConfig).where(DataSourceConfig.provider == provider))
        return self._prepare_credentials(provider, credentials, row)

    def _serialize_credentials(self, provider: str, credentials: dict[str, str]) -> tuple[str | None, str | None, str | None]:
        values = {str(key): str(value) for key, value in credentials.items() if value}
        if not values:
            return None, None, None
        plaintext = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        envelope = self.cipher.encrypt(plaintext, aad=b"datahub:credentials:v1")
        encoded = json.dumps(asdict(envelope), ensure_ascii=False, sort_keys=True)
        fingerprint = credential_fingerprint(plaintext)
        definition = get_provider(provider)
        secret_keys = [field.key for field in definition.credential_fields if field.secret]
        hint_value = next((values[key] for key in secret_keys if values.get(key)), next(iter(values.values())))
        return encoded, fingerprint, key_hint(hint_value)

    def _view(self, provider: str, row: DataSourceConfig | None) -> DataSourceView:
        definition = get_provider(provider)
        return DataSourceView(
            id=row.id if row else None,
            provider=provider,
            display_name=definition.display_name,
            description=definition.description,
            capabilities=[cap.value for cap in definition.capabilities],
            auth_type=definition.auth_type,
            credential_fields=[field.model_dump(mode="json") for field in definition.credential_fields],
            fee_type=definition.fee_type,
            update_frequency=definition.update_frequency,
            risk_note=definition.risk_note,
            available=definition.available,
            unavailable_reason=definition.unavailable_reason,
            enabled=row.enabled if row else definition.enabled_by_default,
            version=row.version if row else 0,
            key_hint=row.key_hint if row else None,
            fingerprint=row.fingerprint if row else None,
            last_probe_status=row.last_probe_status if row else None,
            last_probe_at=row.last_probe_at if row else None,
            last_probe_latency_ms=row.last_probe_latency_ms if row else None,
        )

    def list_configs(self) -> list[DataSourceView]:
        rows = {row.provider: row for row in self.db.scalars(select(DataSourceConfig)).all()}
        return [self._view(name, rows.get(name)) for name in sorted(PROVIDER_REGISTRY)]

    def get_config(self, provider: str) -> DataSourceView:
        get_provider(provider)
        row = self.db.scalar(select(DataSourceConfig).where(DataSourceConfig.provider == provider))
        return self._view(provider, row)

    def load_credentials(self, provider: str) -> dict[str, str]:
        """Decrypt a provider credential only for the duration of a probe/call."""
        row = self.db.scalar(select(DataSourceConfig).where(DataSourceConfig.provider == provider))
        return self._decrypt_row_credentials(row)

    def save_config(
        self,
        provider: str,
        *,
        public_config: dict[str, Any] | None,
        credentials: dict[str, str],
        expected_version: int | None,
        actor_id: int | None,
    ) -> DataSourceView:
        get_provider(provider)
        row = self.db.scalar(
            select(DataSourceConfig)
            .where(DataSourceConfig.provider == provider)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        created = row is None
        merged_credentials = self._prepare_credentials(provider, credentials, row)
        if row is None:
            if expected_version not in (None, 0):
                raise DataHubConflict()
            row = DataSourceConfig(provider=provider, enabled=get_provider(provider).enabled_by_default, public_config_json=public_config or {}, version=1, updated_by=actor_id)
            self.db.add(row)
            try:
                self.db.flush()
            except IntegrityError:
                self.db.rollback()
                raise DataHubConflict() from None
        elif expected_version is None or row.version != expected_version:
            raise DataHubConflict()
        if public_config is not None:
            row.public_config_json = public_config
        encrypted, fingerprint, hint = self._serialize_credentials(provider, merged_credentials)
        if encrypted is not None:
            row.encrypted_credentials = encrypted
            row.fingerprint = fingerprint
            row.key_hint = hint
            row.credential_version = "v1"
        if not created:
            row.version = (row.version or 0) + 1
        row.updated_by = actor_id
        self.db.add(DataSourceAuditEvent(actor=actor_id, event="config_saved", config_id=row.id, version=row.version, payload_json={"provider": provider}))
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise DataHubConflict() from None
        self.db.refresh(row)
        return self._view(provider, row)

    def set_enabled(self, provider: str, enabled: bool, *, expected_version: int, actor_id: int | None) -> DataSourceView:
        definition = get_provider(provider)
        if enabled and not definition.available:
            raise DataHubError(DataHubErrorCode.NOT_CONFIGURED, definition.unavailable_reason or "数据源当前不可用")
        row = self.db.scalar(
            select(DataSourceConfig)
            .where(DataSourceConfig.provider == provider)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise DataHubError(DataHubErrorCode.NOT_CONFIGURED, "请先保存数据源配置")
        if row.version != expected_version:
            raise DataHubConflict()
        row.enabled = enabled
        row.version += 1
        row.updated_by = actor_id
        self.db.add(DataSourceAuditEvent(actor=actor_id, event="config_enabled" if enabled else "config_disabled", config_id=row.id, version=row.version))
        self.db.commit()
        self.db.refresh(row)
        return self._view(provider, row)

    def record_probe(self, probe: ProbeRecord, *, actor_id: int | None) -> DataSourceProbeRun:
        row = self.db.scalar(select(DataSourceConfig).where(DataSourceConfig.provider == probe.provider))
        probe_fingerprint = probe.fingerprint or _effective_fingerprint(probe.provider, row)
        run = DataSourceProbeRun(
            provider=probe.provider,
            capability=probe.capability,
            fingerprint=probe_fingerprint,
            contract_version=probe.contract_version,
            status=probe.status,
            rows=probe.rows,
            latency_ms=probe.latency_ms,
            error_code=probe.error_code,
            # Provider samples may contain ``datetime``/Decimal/Pydantic
            # values.  Normalize them before writing JSONB so a real probe
            # cannot turn into a 500 merely because its sample is typed.
            safe_sample_json=_json_safe_payload(probe.safe_sample),
            created_by=actor_id,
        )
        self.db.add(run)
        if row:
            row.last_probe_status = probe.status
            row.last_probe_at = datetime.now(timezone.utc)
            row.last_probe_latency_ms = probe.latency_ms
        self.db.add(DataSourceAuditEvent(actor=actor_id, event="probe_run", config_id=row.id if row else None, payload_json={"provider": probe.provider, "capability": probe.capability, "status": probe.status}))
        self.db.commit()
        self.db.refresh(run)
        return run

    def probe_is_valid(self, provider: str, capability: str, *, contract_version: str = "1.0", now: datetime | None = None) -> bool:
        definition = get_provider(provider)
        row = self.db.scalar(select(DataSourceConfig).where(DataSourceConfig.provider == provider))
        if row is None and (definition.auth_type != "none" or not definition.enabled_by_default):
            return False
        fingerprint = _effective_fingerprint(provider, row)
        now = now or datetime.now(timezone.utc)
        latest = self.db.scalars(
            select(DataSourceProbeRun)
            .where(DataSourceProbeRun.provider == provider, DataSourceProbeRun.capability == capability)
            .order_by(DataSourceProbeRun.created_at.desc())
        ).first()
        if latest is None or latest.status != "ok":
            return False
        created = latest.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return (
            created >= now - self.PROBE_TTL
            and latest.fingerprint == fingerprint
            and latest.contract_version == contract_version
        )

    def save_route(
        self,
        capability: Capability | str,
        *,
        mode: str,
        providers: list[str],
        expected_version: int | None,
        actor_id: int | None,
        contract_version: str = "1.0",
    ) -> DataSourceRoute:
        capability = Capability(capability)
        if mode not in {"auto", "fixed"} or not providers:
            raise DataHubError(DataHubErrorCode.VALIDATION, "路由模式或数据源顺序无效")
        if mode == "fixed" and len(providers) != 1:
            raise DataHubError(DataHubErrorCode.VALIDATION, "固定路由只能选择一个数据源；多个备选请使用自动路由")
        for provider in providers:
            get_provider(provider)
            if capability not in get_provider(provider).capabilities:
                raise DataHubError(DataHubErrorCode.VALIDATION, "数据源不支持该数据能力")
            config = self.db.scalar(select(DataSourceConfig).where(DataSourceConfig.provider == provider))
            definition = get_provider(provider)
            if not definition.available:
                raise DataHubError(DataHubErrorCode.NOT_CONFIGURED, definition.unavailable_reason or "数据源当前不可用")
            if mode == "fixed" and ((config is None and not definition.enabled_by_default) or (config is not None and not config.enabled)):
                raise DataHubError(DataHubErrorCode.NOT_CONFIGURED, "固定路由的数据源尚未启用")
            if mode == "fixed" and not self.probe_is_valid(provider, capability.value, contract_version=contract_version):
                raise DataHubError(DataHubErrorCode.NOT_CONFIGURED, "请先完成该数据源的能力级测试")
        row = self.db.scalar(
            select(DataSourceRoute)
            .where(DataSourceRoute.capability == capability.value)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            if expected_version not in (None, 0):
                raise DataHubConflict()
            row = DataSourceRoute(capability=capability.value, mode=mode, provider_order_json=providers, contract_version=contract_version, version=1, updated_by=actor_id)
            self.db.add(row)
            try:
                self.db.flush()
            except IntegrityError:
                self.db.rollback()
                raise DataHubConflict() from None
        elif expected_version is None or row.version != expected_version:
            raise DataHubConflict()
        else:
            row.mode, row.provider_order_json, row.contract_version, row.version, row.updated_by = mode, providers, contract_version, row.version + 1, actor_id
        self.db.add(DataSourceAuditEvent(actor=actor_id, event="route_saved", route_id=row.id, version=row.version, payload_json={"capability": capability.value, "providers": providers, "mode": mode}))
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise DataHubConflict() from None
        self.db.refresh(row)
        return row


__all__ = ["DataHubConfigService", "DataSourceView", "ProbeRecord"]


def _effective_fingerprint(provider: str, row: DataSourceConfig | None) -> str:
    if row is not None and row.fingerprint:
        return row.fingerprint
    public = json.dumps((row.public_config_json if row is not None else {}) or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{provider}|{public}|v1".encode("utf-8")).hexdigest()[:32]


def _json_safe_payload(value: Any) -> Any:
    if value is None:
        return None
    return json.loads(json.dumps(value, ensure_ascii=False, default=_json_default))


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    enum_value = getattr(value, "value", None)
    if enum_value is not None and not callable(enum_value):
        return enum_value
    return str(value)

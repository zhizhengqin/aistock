from types import SimpleNamespace

import pytest

from app.cli.datahub import _provider, build_parser, live_smoke_async


def test_datahub_cli_requires_provider_and_capability_choices():
    args = build_parser().parse_args(["--provider", "tushare", "--capability", "kpl.limit_list"])
    assert args.provider == "tushare"
    assert args.capability == "kpl.limit_list"


def test_cli_provider_factory_passes_both_native_kpl_credentials():
    provider = _provider("kpl_native", {"user_id": "fake-user", "token": "fake-token"})

    assert provider.user_id == "fake-user"
    assert provider.token == "fake-token"


@pytest.mark.asyncio
@pytest.mark.parametrize("credentials", [{"token": "fake-token"}, {"user_id": "fake-user"}])
async def test_live_smoke_skips_partial_required_credentials_without_network(credentials):
    class PartialService:
        def __init__(self, *args, **kwargs):
            pass

        def load_credentials(self, provider):
            return credentials

    def should_not_build_provider(provider, loaded_credentials):
        raise AssertionError("partial credentials must not start a live request")

    result = await live_smoke_async(
        provider="kpl_native",
        capability="kpl_native.stock_tags",
        session_factory=lambda: SimpleNamespace(close=lambda: None),
        provider_factory=should_not_build_provider,
        config_service_factory=lambda _session: PartialService(),
    )

    assert result.status == "not_configured"
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_live_smoke_reports_missing_paid_credentials_without_network():
    class EmptyService:
        def __init__(self, *args, **kwargs):
            pass

        def load_credentials(self, provider):
            return {}

    result = await live_smoke_async(
        provider="tushare",
        capability="kpl.limit_list",
        session_factory=lambda: SimpleNamespace(close=lambda: None),
        config_service_factory=lambda _session: EmptyService(),
    )
    assert result.status == "not_configured"
    assert result.exit_code == 0
    assert "Token" not in result.rendered

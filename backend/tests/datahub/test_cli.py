from types import SimpleNamespace

import pytest

from app.cli.datahub import build_parser, live_smoke_async


def test_datahub_cli_requires_provider_and_capability_choices():
    args = build_parser().parse_args(["--provider", "tushare", "--capability", "kpl.limit_list"])
    assert args.provider == "tushare"
    assert args.capability == "kpl.limit_list"


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

from app.datahub.contracts import Capability
from app.datahub.registry import PROVIDER_REGISTRY, ProviderDefinition, get_provider, providers_for


def test_registry_is_single_source_of_truth_for_provider_explanations_and_capabilities():
    tencent = get_provider("tencent")
    assert isinstance(tencent, ProviderDefinition)
    assert tencent.display_name == "腾讯财经"
    assert tencent.auth_type == "none"
    assert Capability.MARKET_INDICES in tencent.capabilities
    assert tencent.enabled_by_default is True
    assert "token" not in str(tencent.model_dump()).lower()


def test_kpl_native_provider_is_present_but_disabled_by_default():
    kpl = get_provider("kpl_native")
    assert kpl.enabled_by_default is False
    assert kpl.auth_type == "token"
    assert Capability.KPL_NATIVE_STOCK_TAGS in kpl.capabilities
    assert providers_for(Capability.KPL_NATIVE_STOCK_TAGS)[0].name == "kpl_native"


def test_tushare_and_native_kpl_credential_help_is_explicitly_distinct():
    tushare = get_provider("tushare")
    native = get_provider("kpl_native")

    assert "不是开盘啦原生 Token" in tushare.credential_fields[0].help
    assert "只提供 Tushare 的 kpl_* 数据集" in tushare.description
    assert "不能填 Tushare Token" in native.description
    assert "不能填 Tushare Token" in native.credential_fields[1].help


def test_registry_contains_all_twenty_first_phase_capabilities():
    capabilities = {cap for provider in PROVIDER_REGISTRY.values() for cap in provider.capabilities}
    assert set(Capability) <= capabilities


def test_market_board_routes_keep_eastmoney_first_and_sina_as_fallback():
    for capability in (Capability.MARKET_BOARD_QUOTES, Capability.MARKET_BOARD_CONSTITUENTS):
        assert [provider.name for provider in providers_for(capability)] == ["eastmoney", "sina"]


def test_default_market_board_routes_use_sina_after_eastmoney():
    from app.datahub.platform import default_routes

    routes = default_routes()
    assert routes[Capability.MARKET_BOARD_QUOTES].providers == ["eastmoney", "sina"]
    assert routes[Capability.MARKET_BOARD_CONSTITUENTS].providers == ["eastmoney", "sina"]

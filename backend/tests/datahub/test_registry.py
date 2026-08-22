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
    assert Capability.MARKET_AUCTION_OPEN in kpl.capabilities
    assert providers_for(Capability.MARKET_AUCTION_OPEN)[0].name == "kpl_native"


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

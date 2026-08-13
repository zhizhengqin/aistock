import pytest
from types import MappingProxyType

from app.services.llm.providers import PROVIDER_REGISTRY, ProviderProfile
from app.services.llm.types import Provider


def test_registry_contains_immutable_official_defaults():
    assert PROVIDER_REGISTRY == {
        Provider.DEEPSEEK: ProviderProfile(
            "https://api.deepseek.com", frozenset({"api.deepseek.com"}), 16
        ),
        Provider.KIMI: ProviderProfile(
            "https://api.moonshot.cn/v1", frozenset({"api.moonshot.cn"}), 16
        ),
        Provider.QWEN: ProviderProfile(
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            frozenset({"dashscope.aliyuncs.com"}),
            24,
        ),
    }
    with pytest.raises(AttributeError):
        PROVIDER_REGISTRY[Provider.DEEPSEEK].default_base_url = "https://example.com"


def test_registry_outer_mapping_is_read_only():
    assert isinstance(PROVIDER_REGISTRY, MappingProxyType)
    with pytest.raises(TypeError):
        PROVIDER_REGISTRY[Provider.DEEPSEEK] = PROVIDER_REGISTRY[Provider.KIMI]

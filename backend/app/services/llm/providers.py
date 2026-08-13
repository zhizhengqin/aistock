"""Provider profiles for the OpenAI-compatible model adapters.

The registry is intentionally a small immutable protocol description.  It is
used by both URL validation and the HTTP adapter, so provider-specific policy
does not get duplicated in callers.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.llm.types import Provider


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    """Immutable defaults and conservative request overhead for a provider.

    ``default_base_url`` is kept as the first field to preserve the positional
    construction documented in the production model-center plan.  The
    compatibility properties below make the intent explicit for callers that
    use ``base_url``/``input_overhead_tokens`` terminology.
    """

    default_base_url: str
    allowed_hosts: frozenset[str]
    input_overhead_tokens: int

    @property
    def base_url(self) -> str:
        return self.default_base_url

    @property
    def fixed_overhead_tokens(self) -> int:
        return self.input_overhead_tokens

    @property
    def request_overhead_tokens(self) -> int:
        return self.input_overhead_tokens

    @property
    def overhead_tokens(self) -> int:
        return self.input_overhead_tokens

    @property
    def input_overhead_bytes(self) -> int:
        # Kept as a named alias for callers that budget serialized bytes
        # conservatively; the registry values are deliberately tiny protocol
        # overheads and are not a provider context-window limit.
        return self.input_overhead_tokens


PROVIDER_REGISTRY: dict[Provider, ProviderProfile] = {
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


def provider_profile(provider: Provider | str) -> ProviderProfile:
    """Return the immutable profile or raise a safe configuration error."""

    try:
        key = provider if isinstance(provider, Provider) else Provider(provider)
        return PROVIDER_REGISTRY[key]
    except (KeyError, TypeError, ValueError):
        raise ValueError("不支持的大模型供应商") from None


__all__ = ["PROVIDER_REGISTRY", "ProviderProfile", "provider_profile"]

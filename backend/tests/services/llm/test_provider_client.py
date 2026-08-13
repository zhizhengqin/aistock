import json

import httpx
import pytest

from app.services.llm.provider_client import ProviderClient
from app.services.llm.types import LlmRuntimeConfig, Provider


def runtime(provider, base_url, *, max_output_tokens=200):
    return LlmRuntimeConfig(
        config_id=None,
        provider=provider,
        display_name="test",
        model_name="model-x",
        base_url=base_url,
        api_key="sk-test-secret",
        credential_version="v1",
        max_output_tokens=max_output_tokens,
        input_price_micro_yuan_per_million=1,
        output_price_micro_yuan_per_million=2,
        runtime_fingerprint="fp-test",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "base_url", "endpoint"),
    [
        (Provider.DEEPSEEK, "https://api.deepseek.com", "https://api.deepseek.com/chat/completions"),
        (Provider.KIMI, "https://api.moonshot.cn/v1", "https://api.moonshot.cn/v1/chat/completions"),
        (
            Provider.QWEN,
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        ),
    ],
)
async def test_complete_json_uses_exact_compatible_protocol(provider, base_url, endpoint):
    captured = {}

    async def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "model-x",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=True)
    result = await ProviderClient(client=client).complete_json(
        runtime(provider, base_url), [{"role": "user", "content": "hello"}]
    )
    await client.aclose()
    assert captured["url"] == endpoint
    assert captured["auth"] == "Bearer sk-test-secret"
    assert captured["payload"]["max_tokens"] == 200
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert result.result_json == {"ok": True}
    assert result.prompt_tokens == 7
    assert result.completion_tokens == 3


@pytest.mark.asyncio
async def test_provider_error_never_exposes_api_key():
    async def handler(request):
        return httpx.Response(401, text="bad key sk-test-secret")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(Exception) as exc:
        await ProviderClient(client=client).complete_json(
            runtime(Provider.DEEPSEEK, "https://api.deepseek.com"),
            [{"role": "user", "content": "hello"}],
        )
    await client.aclose()
    assert exc.value.code == "llm_auth_error"
    assert "sk-test-secret" not in str(exc.value)


@pytest.mark.asyncio
async def test_oversized_response_is_bounded():
    async def handler(request):
        return httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(Exception) as exc:
        await ProviderClient(client=client).complete_json(
            runtime(Provider.DEEPSEEK, "https://api.deepseek.com"),
            [{"role": "user", "content": "hello"}],
        )
    await client.aclose()
    assert exc.value.code == "llm_response_too_large"

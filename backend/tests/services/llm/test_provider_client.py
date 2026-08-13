import json

import httpx
import httpcore
import pytest

from app.services.llm.provider_client import (
    ProviderClient,
    _PINNED_IP,
    _PinnedNetworkBackend,
    _PinnedAsyncHTTPTransport,
)
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
    assert exc.value.code == "llm_auth_failed"
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


@pytest.mark.asyncio
async def test_transport_connects_to_the_verified_ip_while_preserving_hostname():
    class Delegate:
        def __init__(self):
            self.calls = []

        async def connect_tcp(self, host, port, **kwargs):
            self.calls.append((host, port))
            return object()

    delegate = Delegate()
    backend = _PinnedNetworkBackend(delegate)
    token = _PINNED_IP.set("8.8.8.8")
    try:
        await backend.connect_tcp("api.deepseek.com", 443)
    finally:
        _PINNED_IP.reset(token)
    assert delegate.calls == [("8.8.8.8", 443)]


@pytest.mark.asyncio
async def test_pinned_transport_connects_to_verified_ip_but_sends_original_host_and_sni():
    class Stream(httpcore.AsyncNetworkStream):
        def __init__(self, calls):
            self.calls = calls
            self.response = bytearray(
                b"HTTP/1.1 200 OK\r\nContent-Length: 53\r\n"
                b"Content-Type: application/json\r\n\r\n"
                b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'
            )

        async def read(self, max_bytes, timeout=None):
            if not self.response:
                return b""
            chunk = bytes(self.response[:max_bytes])
            del self.response[:max_bytes]
            return chunk

        async def write(self, buffer, timeout=None):
            self.calls.append(("write", bytes(buffer)))

        async def aclose(self):
            return None

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            self.calls.append(("sni", server_hostname))
            return self

    class Backend(httpcore.AsyncNetworkBackend):
        def __init__(self):
            self.calls = []

        async def connect_tcp(self, host, port, **kwargs):
            self.calls.append(("tcp", host, port))
            return Stream(self.calls)

        async def connect_unix_socket(self, path, **kwargs):
            raise AssertionError("unexpected unix socket")

        async def sleep(self, seconds):
            return None

    backend = Backend()
    transport = _PinnedAsyncHTTPTransport(network_backend=backend)
    client = httpx.AsyncClient(transport=transport, verify=True, trust_env=False)
    resolver_calls = []

    def resolver(host, port):
        resolver_calls.append((host, port))
        return ["8.8.8.8"] if len(resolver_calls) == 1 else ["10.0.0.1"]

    result = await ProviderClient(
        client=client,
        resolver=resolver,
    ).complete_json(
        runtime(Provider.DEEPSEEK, "https://api.deepseek.com"),
        [{"role": "user", "content": "hello"}],
    )
    await client.aclose()
    assert result.result_json == {"ok": True}
    assert resolver_calls == [("api.deepseek.com", 443)]
    assert ("tcp", "8.8.8.8", 443) in backend.calls
    assert ("sni", "api.deepseek.com") in backend.calls
    writes = [entry[1] for entry in backend.calls if entry[0] == "write"]
    assert any(b"Host: api.deepseek.com" in payload for payload in writes)


@pytest.mark.asyncio
async def test_streaming_response_stops_before_consuming_tail_after_two_mib():
    class Stream(httpx.AsyncByteStream):
        def __init__(self):
            self.consumed = []

        async def __aiter__(self):
            self.consumed.append("head")
            yield b"x" * (2 * 1024 * 1024 + 1)
            self.consumed.append("tail")
            yield b"tail"

        async def aclose(self):
            return None

    stream = Stream()

    async def handler(request):
        return httpx.Response(200, stream=stream)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(Exception) as exc:
        await ProviderClient(client=client).complete_json(
            runtime(Provider.DEEPSEEK, "https://api.deepseek.com"),
            [{"role": "user", "content": "hello"}],
        )
    await client.aclose()
    assert exc.value.code == "llm_response_too_large"
    assert stream.consumed == ["head"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, "upstream secret sk-test-secret", "llm_auth_failed"),
        (402, '{"error":{"code":"insufficient_quota","message":"balance sk-test-secret"}}', "llm_quota_exceeded"),
        (503, "upstream secret sk-test-secret", "llm_unavailable"),
    ],
)
async def test_provider_errors_use_stable_codes_and_redact_upstream_body(status, body, code):
    async def handler(request):
        return httpx.Response(status, content=body.encode("utf-8"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(Exception) as exc:
        await ProviderClient(client=client).complete_json(
            runtime(Provider.DEEPSEEK, "https://api.deepseek.com"),
            [{"role": "user", "content": "hello"}],
        )
    await client.aclose()
    assert exc.value.code == code
    assert "sk-test-secret" not in str(exc.value)
    assert "upstream secret" not in str(exc.value)


@pytest.mark.asyncio
async def test_malformed_provider_json_uses_stable_invalid_response_code():
    async def handler(request):
        return httpx.Response(200, content=b"not-json")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(Exception) as exc:
        await ProviderClient(client=client).complete_json(
            runtime(Provider.DEEPSEEK, "https://api.deepseek.com"),
            [{"role": "user", "content": "hello"}],
        )
    await client.aclose()
    assert exc.value.code == "llm_invalid_response"

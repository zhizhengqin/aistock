import httpx
import pytest

from app.services.llm import http_client


@pytest.fixture(autouse=True)
async def reset_http_client():
    await http_client.close_llm_http_client()
    yield
    await http_client.close_llm_http_client()


@pytest.mark.asyncio
async def test_shared_client_reused_with_safe_defaults():
    first = http_client.get_llm_http_client()
    second = http_client.get_llm_http_client()

    assert first is second
    assert isinstance(first, httpx.AsyncClient)
    assert first.trust_env is False
    assert first.follow_redirects is False
    assert first.timeout.connect == 5
    assert first.timeout.read == 60
    assert first.timeout.write == 10
    assert first.timeout.pool == 5

    pool = first._transport._pool
    assert pool._max_connections == 20
    assert pool._max_keepalive_connections == 10


@pytest.mark.asyncio
async def test_close_is_idempotent():
    client = http_client.get_llm_http_client()
    await http_client.close_llm_http_client()
    await http_client.close_llm_http_client()
    assert client.is_closed

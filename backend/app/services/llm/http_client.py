"""One process-scoped, tightly bounded HTTP client for provider calls."""

from __future__ import annotations

import httpx


_llm_http_client: httpx.AsyncClient | None = None


def _new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        timeout=httpx.Timeout(connect=5, read=60, write=10, pool=5),
        trust_env=False,
        follow_redirects=False,
    )


def get_llm_http_client() -> httpx.AsyncClient:
    """Return the process-wide provider client, creating it on first use."""

    global _llm_http_client
    if _llm_http_client is None or _llm_http_client.is_closed:
        _llm_http_client = _new_client()
    return _llm_http_client


async def close_llm_http_client() -> None:
    """Close the shared client; safe to call repeatedly from both lifecycles."""

    global _llm_http_client
    client = _llm_http_client
    if client is None:
        return
    try:
        if not client.is_closed:
            await client.aclose()
    finally:
        # Resetting permits a fresh FastAPI/ARQ lifecycle in the same process
        # (tests and development reloads) without returning a closed client.
        _llm_http_client = None


__all__ = ["close_llm_http_client", "get_llm_http_client"]

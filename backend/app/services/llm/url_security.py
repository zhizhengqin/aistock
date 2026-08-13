"""SSRF-safe provider URL validation and DNS checks.

The validator has no network side effects by default, which keeps configuration
validation deterministic.  ``ProviderClient`` calls it with ``resolve=True``
immediately before each physical HTTP request.  Keeping the pure URL policy
separate from DNS resolution also makes the policy straightforward to test.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

from app.services.llm.errors import LlmError
from app.services.llm.providers import provider_profile
from app.services.llm.types import Provider


Resolver = Callable[[str, int], Iterable[object]]


def _error(code: str, message: str) -> LlmError:
    error = LlmError(message, code=code)
    error.retryable = False
    error.user_message = message
    error.confirmed_unsent = True
    error.may_have_sent = False
    return error


def canonicalize_hostname(hostname: str) -> str:
    """Canonicalise one DNS name using IDNA and DNS label boundaries."""

    if not isinstance(hostname, str) or not hostname:
        raise _error("llm_url_invalid", "大模型地址无效")
    if "%" in hostname:
        # Percent-encoded host labels are ambiguous and must not be decoded
        # before the allow-list check.
        raise _error("llm_url_invalid", "大模型地址中的域名编码不受支持")
    try:
        canonical = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        raise _error("llm_url_invalid", "大模型地址中的域名无效") from None
    if not canonical or canonical.endswith("."):
        raise _error("llm_url_invalid", "大模型地址中的域名无效")
    # An IP literal is never accepted, even if it happens to be in an official
    # suffix.  ``ip_address`` handles both IPv4 and bracket-free IPv6 forms.
    try:
        ipaddress.ip_address(canonical)
    except ValueError:
        return canonical
    raise _error("llm_url_private_host", "大模型地址不允许使用 IP 字面量")


def _provider_key(provider: Provider | str) -> Provider:
    try:
        return provider if isinstance(provider, Provider) else Provider(provider)
    except (TypeError, ValueError):
        raise _error("llm_url_invalid", "大模型供应商无效") from None


def _host_allowed(provider: Provider, hostname: str, allowed_hosts: set[str] | None) -> bool:
    profile = provider_profile(provider)
    allowed = {canonicalize_hostname(host) for host in (allowed_hosts or profile.allowed_hosts)}
    if provider in {Provider.DEEPSEEK, Provider.KIMI}:
        return hostname in allowed
    # DashScope's public business-space endpoints are explicitly allowed by
    # suffix.  The leading-dot test prevents ``evil-dashscope.aliyuncs.com``
    # from being accepted as a subdomain.
    return hostname in allowed or any(
        hostname.endswith(f".{suffix}")
        for suffix in ("maas.aliyuncs.com", "dashscope.aliyuncs.com")
    )


def _path_allowed(provider: Provider, path: str) -> bool:
    normalized = path or ""
    if normalized.endswith("/") and normalized != "/":
        normalized = normalized.rstrip("/")
    if provider is Provider.DEEPSEEK:
        return normalized in {"", "/", "/v1"}
    if provider is Provider.KIMI:
        return normalized in {"", "/", "/v1"}
    return normalized in {"", "/", "/v1", "/compatible-mode/v1"} or normalized.endswith(
        "/compatible-mode/v1"
    )


def canonicalize_base_url(
    url: str,
    provider: Provider | str,
    *,
    allowed_hosts: set[str] | None = None,
) -> str:
    """Validate URL syntax/policy and return one canonical base URL.

    No query string or fragment is ever retained.  Credentials in a URL are
    rejected rather than silently dropped so a typo cannot become a secret
    disclosure.
    """

    provider_value = _provider_key(provider)
    if not isinstance(url, str) or not url or any(ch.isspace() for ch in url):
        raise _error("llm_url_invalid", "大模型地址无效")
    try:
        parsed = urlsplit(url)
        # Accessing ``port`` eagerly catches malformed/non-numeric ports.
        port = parsed.port
    except ValueError:
        raise _error("llm_url_invalid", "大模型地址端口无效") from None
    if parsed.scheme.lower() != "https":
        raise _error("llm_url_insecure", "大模型地址必须使用 HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise _error("llm_url_invalid", "大模型地址不能包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise _error("llm_url_invalid", "大模型地址不能包含查询参数或片段")
    if port not in (None, 443):
        raise _error("llm_url_insecure", "大模型地址只允许 443 端口")
    try:
        hostname = canonicalize_hostname(parsed.hostname or "")
    except LlmError:
        raise
    if not _host_allowed(provider_value, hostname, allowed_hosts):
        raise _error("llm_url_not_allowed", "大模型地址不在供应商官方域名范围内")
    if not _path_allowed(provider_value, parsed.path):
        raise _error("llm_url_path_not_allowed", "大模型地址路径不受支持")
    path = parsed.path or ""
    if path not in {"", "/"}:
        path = path.rstrip("/")
    return urlunsplit(("https", hostname, path, "", ""))


def _extract_ip(value: object) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return value
    if isinstance(value, str):
        return ipaddress.ip_address(value.split("%", 1)[0])
    if isinstance(value, tuple) and value:
        # ``getaddrinfo`` records are five-tuples whose final item is the
        # sockaddr; a plain sockaddr is itself a two/ four-tuple.
        if len(value) >= 5 and isinstance(value[0], int):
            return _extract_ip(value[-1])
        return _extract_ip(value[0])
    # getaddrinfo returns (family, type, proto, canonname, sockaddr).
    if isinstance(value, list) and value:
        return _extract_ip(value[-1])
    raise ValueError("invalid address")


def is_public_address(address: object) -> bool:
    """Return whether an A/AAAA result is safe for an outbound call."""

    try:
        ip = _extract_ip(address)
    except (ValueError, TypeError):
        return False
    # ``is_global`` excludes RFC1918, loopback, link-local, documentation,
    # unspecified and reserved ranges on supported Python versions.  Keep the
    # explicit checks for mapped/edge values and future stdlib changes.
    return bool(
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_reserved
        and not ip.is_multicast
        and not ip.is_unspecified
    )


def _default_resolver(hostname: str, port: int) -> list[object]:
    return list(
        socket.getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    )


def resolve_public_addresses(
    hostname: str,
    *,
    port: int = 443,
    resolver: Resolver | None = None,
) -> tuple[str, ...]:
    """Resolve every A/AAAA result and reject any non-public address."""

    resolver = resolver or _default_resolver
    try:
        results = list(resolver(hostname, port))
    except Exception:
        raise _error("llm_dns_unavailable", "大模型地址无法解析") from None
    if not results:
        raise _error("llm_dns_unavailable", "大模型地址无法解析")
    addresses: list[str] = []
    for result in results:
        try:
            address = _extract_ip(result)
        except (ValueError, TypeError):
            raise _error("llm_private_host", "大模型地址解析结果无效") from None
        if not is_public_address(address):
            raise _error("llm_private_host", "大模型地址解析到了非公网地址")
        addresses.append(str(address))
    return tuple(dict.fromkeys(addresses))


def validate_base_url(
    url: str,
    provider: Provider | str,
    *,
    resolver: Resolver | None = None,
    resolve: bool | None = None,
    allowed_hosts: set[str] | None = None,
) -> str:
    """Return canonical URL and optionally perform an immediate DNS check."""

    canonical = canonicalize_base_url(url, provider, allowed_hosts=allowed_hosts)
    # Supplying a deterministic resolver is an explicit request to perform
    # the DNS safety check; production callers pass ``resolve=True`` after
    # canonicalisation.  A resolver-less pure policy check remains offline.
    if resolve or (resolve is None and resolver is not None):
        parsed = urlsplit(canonical)
        resolve_public_addresses(parsed.hostname or "", port=443, resolver=resolver)
    return canonical


async def validate_base_url_async(
    url: str,
    provider: Provider | str,
    *,
    resolver: Resolver | None = None,
    allowed_hosts: set[str] | None = None,
) -> str:
    """Async wrapper that keeps blocking DNS work off the event loop."""

    return await asyncio.to_thread(
        validate_base_url,
        url,
        provider,
        resolver=resolver,
        resolve=True,
        allowed_hosts=allowed_hosts,
    )


__all__ = [
    "canonicalize_base_url",
    "canonicalize_hostname",
    "is_public_address",
    "resolve_public_addresses",
    "validate_base_url",
    "validate_base_url_async",
]

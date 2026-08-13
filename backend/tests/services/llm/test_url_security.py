import ipaddress

import pytest

from app.services.llm.types import Provider
from app.services.llm.url_security import (
    canonicalize_base_url,
    is_public_address,
    resolve_public_addresses,
    validate_base_url,
)


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://api.deepseek.com", "llm_url_insecure"),
        ("https://user:pass@api.deepseek.com", "llm_url_invalid"),
        ("https://api.deepseek.com:8443", "llm_url_insecure"),
        ("https://api.deepseek.com?x=1", "llm_url_invalid"),
        ("https://evil.example", "llm_url_not_allowed"),
        ("https://api.deepseek.com.evil.example", "llm_url_not_allowed"),
    ],
)
def test_provider_url_policy_rejects_unsafe_urls(url, code):
    with pytest.raises(Exception) as exc:
        validate_base_url(url, Provider.DEEPSEEK)
    assert exc.value.code == code


def test_qwen_official_business_suffix_and_unicode_trailing_dot_are_canonicalized():
    assert canonicalize_base_url(
        "https://Foo.MAAS.ALIYUNCS.COM./compatible-mode/v1/", Provider.QWEN
    ) == "https://foo.maas.aliyuncs.com/compatible-mode/v1"


@pytest.mark.parametrize("value", ["127.0.0.1", "10.0.0.1", "::1", "fe80::1", "192.0.2.1"])
def test_non_public_ip_results_are_rejected(value):
    assert not is_public_address(ipaddress.ip_address(value))


def test_mixed_public_private_dns_is_rejected():
    with pytest.raises(Exception) as exc:
        resolve_public_addresses(
            "api.deepseek.com",
            resolver=lambda host, port: ["8.8.8.8", "10.0.0.1"],
        )
    assert exc.value.code == "llm_private_host"


def test_connect_time_dns_revalidation_uses_new_result():
    answers = iter([["8.8.8.8"], ["10.0.0.1"]])
    resolver = lambda host, port: next(answers)
    validate_base_url("https://api.deepseek.com", Provider.DEEPSEEK, resolver=resolver, resolve=True)
    with pytest.raises(Exception) as exc:
        validate_base_url("https://api.deepseek.com", Provider.DEEPSEEK, resolver=resolver, resolve=True)
    assert exc.value.code == "llm_private_host"

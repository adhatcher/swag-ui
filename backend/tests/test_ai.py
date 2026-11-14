import asyncio

import httpx
import pytest

from backend.app.ai import AIUnavailable, redact, suggest


def test_redacts_credential_directives_before_ai_request() -> None:
    excerpt = (
        "proxy_set_header Authorization Bearer abc123;\napi_key=secret123\nproxy_pass http://app;"
    )
    cleaned = redact(excerpt)
    assert "abc123" not in cleaned
    assert "secret123" not in cleaned
    assert "proxy_pass http://app;" in cleaned


def test_redacts_whitespace_separated_nginx_secrets() -> None:
    excerpt = (
        "proxy_set_header X-Api-Key hdrsecret;\n"
        "set $upstream_token tokensecret;\n"
        "auth_basic_user_file /config/nginx/.htpasswd;\n"
        "proxy_set_header Host $host;\n"
    )
    cleaned = redact(excerpt)
    assert "hdrsecret" not in cleaned
    assert "tokensecret" not in cleaned
    assert ".htpasswd" not in cleaned
    assert "proxy_set_header X-Api-Key [REDACTED];" in cleaned
    assert "proxy_set_header Host $host;" in cleaned


def test_litellm_receives_only_redacted_excerpt(monkeypatch) -> None:
    captured = {}
    actual_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "Use proxy_pass."}}]})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: actual_client(transport=httpx.MockTransport(handler))
    )
    result = asyncio.run(
        suggest(
            base_url="http://litellm/v1",
            api_key="test-key",
            model="local-model",
            excerpt="api_key=topsecret\nproxy_pass http://app;",
            question="token=questionsecret\nReview this",
        )
    )
    assert result == {"suggestion": "Use proxy_pass."}
    assert "topsecret" not in captured["body"]
    assert "questionsecret" not in captured["body"]
    assert "proxy_pass" in captured["body"]


def test_litellm_unconfigured_or_malformed_response(monkeypatch) -> None:
    with pytest.raises(AIUnavailable, match="not configured"):
        asyncio.run(
            suggest(base_url=None, api_key=None, model=None, excerpt="server {}", question="Review")
        )

    actual_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: actual_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": []}))
        ),
    )
    with pytest.raises(AIUnavailable, match="usable suggestion"):
        asyncio.run(
            suggest(
                base_url="http://litellm/v1",
                api_key=None,
                model="local",
                excerpt="server {}",
                question="Review",
            )
        )

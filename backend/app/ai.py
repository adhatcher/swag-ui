"""Optional LiteLLM advice. Model output never becomes a file write."""

import re

import httpx


class AIUnavailable(RuntimeError):
    pass


SENSITIVE = r"(?:password|passwd|secret|token|api[_-]?key|authorization|cookie)"
REDACTIONS = [
    re.compile(rf"(?im)^(\s*[^#\n]*{SENSITIVE}[^\n]*?[=:])\s*[^;\n]+"),
    # Nginx directives separate arguments with whitespace, e.g.
    # `proxy_set_header X-Api-Key value;` or `set $api_token value;`.
    re.compile(rf"(?im)^([ \t]*[A-Za-z_]+[ \t]+[^\s;]*{SENSITIVE}[^\s;]*)[ \t]+[^;\n]+"),
    re.compile(r"(?im)^([ \t]*auth_basic_user_file)[ \t]+[^;\n]+"),
]


def redact(text: str) -> str:
    for pattern in REDACTIONS:
        text = pattern.sub(r"\1 [REDACTED]", text)
    return text


async def suggest(
    *,
    base_url: str | None,
    api_key: str | None,
    model: str | None,
    excerpt: str,
    question: str,
) -> dict:
    if not base_url or not model:
        raise AIUnavailable("LiteLLM is not configured")
    if len(excerpt) > 12_000 or len(question) > 2_000:
        raise ValueError("AI request is too long")
    messages = [
        {
            "role": "system",
            "content": (
                "You advise on Nginx/SWAG configuration. Explain risks and return suggestions "
                "as text. Never claim changes were applied."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{redact(question)}\n\n"
                f"Selected configuration excerpt:\n{redact(excerpt)}"
            ),
        },
    ]
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                base_url.rstrip("/") + "/chat/completions",
                json={"model": model, "messages": messages, "temperature": 0.2},
                headers=headers,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("Invalid LiteLLM response")
            return {"suggestion": content}
    except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
        raise AIUnavailable("LiteLLM did not return a usable suggestion") from exc

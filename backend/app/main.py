"""Authenticated API and frontend for private SWAG administration."""

import asyncio
import os
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.app import swag
from backend.app.ai import AIUnavailable, redact, suggest
from backend.app.drafts import DraftStore
from backend.app.files import InvalidProxyFile, list_proxies, read_proxy
from backend.app.settings import Settings


class HostCheckMiddleware(TrustedHostMiddleware):
    """Trusted-host check that lets the container healthcheck reach /healthz.

    The Docker HEALTHCHECK calls http://127.0.0.1:8080/healthz, whose Host header
    is rarely in SWAG_UI_ALLOWED_HOSTS. /healthz returns no data, so skipping the
    check for that one path exposes nothing.
    """

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"] == "/healthz":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


class LoginBody(BaseModel):
    password: str


class DraftBody(BaseModel):
    name: str
    content: str
    base_hash: str


class SuggestBody(BaseModel):
    name: str
    excerpt: str = Field(max_length=12_000)
    question: str = Field(max_length=2_000)


class PreviewBody(BaseModel):
    excerpt: str = Field(max_length=12_000)
    question: str = Field(max_length=2_000)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # Check the folder and the files an older image may have left with another owner.
    for path in (settings.data_dir, settings.data_dir / "swag-ui.sqlite3", settings.backup_dir):
        if path.exists() and not os.access(path, os.W_OK):
            raise ValueError(
                f"{path} is not writable by uid {os.geteuid()}; "
                "chown -R the data folder to the container user "
                "(SWAG's PUID:PGID, 99:100 by default)"
            )
    app = FastAPI(title="SWAG UI", docs_url=None, redoc_url=None)
    app.add_middleware(HostCheckMiddleware, allowed_hosts=list(settings.allowed_hosts))
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="strict",
        https_only=settings.cookie_secure,
        max_age=8 * 60 * 60,
    )
    store = DraftStore(settings.data_dir / "swag-ui.sqlite3")
    attempts: dict[str, list[float]] = {}

    def require_auth(request: Request) -> None:
        if not request.session.get("admin"):
            raise HTTPException(401, "Authentication required")

    def require_csrf(request: Request, _auth: None = Depends(require_auth)) -> None:
        token = request.session.get("csrf")
        if not token or not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), token):
            raise HTTPException(403, "Invalid CSRF token")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/api/session")
    def session(request: Request) -> dict:
        if not request.session.get("csrf"):
            request.session["csrf"] = secrets.token_urlsafe(32)
        if request.session.get("admin"):
            return {"authenticated": True, "csrf_token": request.session.get("csrf")}
        return {"authenticated": False, "csrf_token": request.session["csrf"]}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request) -> dict:
        token = request.session.get("csrf")
        if not token or not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), token):
            raise HTTPException(403, "Invalid CSRF token")
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        recent = [item for item in attempts.get(client, []) if now - item < 300]
        if len(recent) >= 5:
            raise HTTPException(429, "Too many login attempts")
        if not secrets.compare_digest(body.password, settings.admin_password):
            attempts[client] = recent + [now]
            raise HTTPException(401, "Invalid password")
        attempts.pop(client, None)
        request.session.clear()
        request.session["admin"] = True
        request.session["csrf"] = secrets.token_urlsafe(32)
        return {"authenticated": True, "csrf_token": request.session["csrf"]}

    @app.post("/api/logout", dependencies=[Depends(require_csrf)])
    def logout(request: Request) -> dict:
        request.session.clear()
        return {"authenticated": False}

    @app.get("/api/overview", dependencies=[Depends(require_auth)])
    async def overview() -> dict:
        status = await asyncio.to_thread(
            swag.status,
            swag_name=settings.swag_container_name,
            tailscale_name=settings.tailscale_container_name,
            cert_dir=settings.cert_dir,
        )
        included = status.get("included_proxies")
        return {
            "proxies": [
                {"name": name, "active": name in included if included is not None else None}
                for name in list_proxies(settings.proxy_dir)
            ],
            "certificates": status.get("certificates", []),
            "tailscale": status.get("tailscale", {"state": "unavailable"}),
            "swag": status.get("swag", {"state": "unavailable", "auto_reload": "unknown"}),
        }

    @app.get("/api/proxies/{name}", dependencies=[Depends(require_auth)])
    def proxy(name: str) -> dict:
        try:
            content, digest = read_proxy(settings.proxy_dir, name)
        except (InvalidProxyFile, FileNotFoundError, OSError) as exc:
            raise HTTPException(404, "Proxy file unavailable") from exc
        return {"name": name, "content": content, "sha256": digest}

    @app.post("/api/drafts", dependencies=[Depends(require_csrf)])
    def create_draft(body: DraftBody) -> dict:
        try:
            draft = store.create(settings.proxy_dir, body.name, body.content, body.base_hash)
        except (InvalidProxyFile, FileNotFoundError, OSError) as exc:
            raise HTTPException(404, "Proxy file unavailable") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": draft.id, "diff": draft.diff}

    @app.get("/api/drafts/{draft_id}", dependencies=[Depends(require_auth)])
    def get_draft(draft_id: str) -> dict:
        draft = store.get(draft_id)
        if not draft:
            raise HTTPException(404, "Draft unavailable")
        return draft.__dict__

    @app.post("/api/drafts/{draft_id}/apply", dependencies=[Depends(require_csrf)])
    async def apply_draft(draft_id: str) -> dict:
        draft = store.get(draft_id)
        if not draft:
            raise HTTPException(404, "Draft unavailable")
        result = await asyncio.to_thread(
            swag.activate,
            draft.name,
            draft.content,
            draft.base_hash,
            proxy_dir=settings.proxy_dir,
            backup_dir=settings.backup_dir,
            swag_name=settings.swag_container_name,
        )
        store.audit(draft_id, result.get("status", "unknown"))
        return result

    @app.post("/api/ai/suggest", dependencies=[Depends(require_csrf)])
    async def ai_suggest(body: SuggestBody) -> dict:
        try:
            read_proxy(settings.proxy_dir, body.name)
            return await suggest(
                base_url=settings.litellm_url,
                api_key=settings.litellm_key,
                model=settings.litellm_model,
                excerpt=body.excerpt,
                question=body.question,
            )
        except (InvalidProxyFile, FileNotFoundError, OSError) as exc:
            raise HTTPException(404, "Proxy file unavailable") from exc
        except AIUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.post("/api/ai/preview", dependencies=[Depends(require_csrf)])
    def ai_preview(body: PreviewBody) -> dict:
        return {
            "redacted_excerpt": redact(body.excerpt),
            "redacted_question": redact(body.question),
        }

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        def index(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(404)
            return FileResponse(dist / "index.html")

    return app

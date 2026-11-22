import os
import sqlite3
from pathlib import Path

import docker
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.settings import Settings

ORIGINAL = "server { listen 443; }\n"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "proxy_dir": tmp_path / "proxy-confs",
        "data_dir": tmp_path / "data",
        "backup_dir": tmp_path / "data" / "backups",
        "cert_dir": tmp_path / "certs",
        "swag_container_name": "swag",
        "tailscale_container_name": None,
        "admin_password": "very-long-test-password",
        "session_secret": "a" * 32,
        "cookie_secure": False,
        "allowed_hosts": ("testserver",),
        "litellm_url": None,
        "litellm_key": None,
        "litellm_model": None,
    }
    values.update(overrides)
    return Settings(**values)


def make_proxy(tmp_path: Path) -> Path:
    proxy_dir = tmp_path / "proxy-confs"
    proxy_dir.mkdir()
    (proxy_dir / "one.subdomain.conf").write_text(ORIGINAL)
    return proxy_dir


def login(client: TestClient, settings: Settings) -> dict:
    token = client.get("/api/session").json()["csrf_token"]
    token = client.post(
        "/api/login",
        json={"password": settings.admin_password},
        headers={"X-CSRF-Token": token},
    ).json()["csrf_token"]
    return {"X-CSRF-Token": token}


def create_draft(client: TestClient, headers: dict) -> str:
    proxy = client.get("/api/proxies/one.subdomain.conf").json()
    body = {"name": proxy["name"], "content": "edited\n", "base_hash": proxy["sha256"]}
    return client.post("/api/drafts", json=body, headers=headers).json()["id"]


def audit_outcomes(settings: Settings, draft_id: str) -> list[str]:
    with sqlite3.connect(settings.data_dir / "swag-ui.sqlite3") as conn:
        rows = conn.execute("SELECT outcome FROM audit WHERE draft_id=?", (draft_id,))
        return [row[0] for row in rows]


@pytest.fixture
def no_docker(monkeypatch):
    def unavailable():
        raise docker.errors.DockerException("no socket")

    monkeypatch.setattr("backend.app.swag.docker_client", unavailable)


def test_authenticated_draft_flow_and_path_denial(tmp_path: Path, no_docker) -> None:
    proxy_dir = make_proxy(tmp_path)
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/proxies/one.subdomain.conf").status_code == 401
        anonymous = client.get("/api/session").json()
        token = anonymous["csrf_token"]
        assert (
            client.post("/api/login", json={"password": settings.admin_password}).status_code == 403
        )
        login_response = client.post(
            "/api/login",
            json={"password": settings.admin_password},
            headers={"X-CSRF-Token": token},
        )
        assert login_response.status_code == 200
        token = login_response.json()["csrf_token"]
        proxy = client.get("/api/proxies/one.subdomain.conf").json()
        assert client.get("/api/proxies/%2E%2E%2Fsecret.conf").status_code in {400, 404}
        body = {"name": proxy["name"], "content": "edited\n", "base_hash": proxy["sha256"]}
        assert client.post("/api/drafts", json=body).status_code == 403
        draft = client.post(
            "/api/drafts",
            json=body,
            headers={"X-CSRF-Token": token},
        )
        assert draft.status_code == 200
        assert (proxy_dir / proxy["name"]).read_text() == proxy["content"]
        assert client.get("/api/drafts/" + draft.json()["id"]).status_code == 200
        assert (
            client.post(
                "/api/drafts/" + draft.json()["id"] + "/apply",
                json={},
                headers={"X-CSRF-Token": token},
            ).status_code
            == 200
        )
        preview = client.post(
            "/api/ai/preview",
            json={"excerpt": "api_key=topsecret", "question": "token=questionsecret"},
            headers={"X-CSRF-Token": token},
        )
        assert preview.status_code == 200
        assert "topsecret" not in preview.json()["redacted_excerpt"]
        assert "questionsecret" not in preview.json()["redacted_question"]


def test_overview_and_apply_without_docker(tmp_path: Path, no_docker) -> None:
    proxy_dir = make_proxy(tmp_path)
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client, settings)
        draft_id = create_draft(client, headers)

        apply = client.post(f"/api/drafts/{draft_id}/apply", json={}, headers=headers)
        overview = client.get("/api/overview")

    assert apply.status_code == 200
    assert apply.json() == {"status": "blocked", "message": "SWAG preflight unavailable"}
    assert overview.status_code == 200
    assert overview.json()["swag"] == {"state": "unavailable", "auto_reload": "unknown"}
    assert overview.json()["proxies"] == [{"name": "one.subdomain.conf", "active": None}]
    assert (proxy_dir / "one.subdomain.conf").read_text() == ORIGINAL
    assert audit_outcomes(settings, draft_id) == ["blocked"]


def test_apply_passes_settings_to_swag(tmp_path: Path, monkeypatch) -> None:
    make_proxy(tmp_path)
    settings = make_settings(tmp_path, swag_container_name="swag-test")
    seen: dict = {}

    def fake_activate(name: str, content: str, base_hash: str, **kwargs) -> dict:
        seen.update(kwargs, name=name, content=content)
        return {"status": "reload_requested", "message": "ok"}

    monkeypatch.setattr("backend.app.swag.activate", fake_activate)
    with TestClient(create_app(settings)) as client:
        headers = login(client, settings)
        draft_id = create_draft(client, headers)

        apply = client.post(f"/api/drafts/{draft_id}/apply", json={}, headers=headers)

    assert apply.status_code == 200
    assert apply.json()["status"] == "reload_requested"
    assert seen["name"] == "one.subdomain.conf"
    assert seen["content"] == "edited\n"
    assert seen["proxy_dir"] == settings.proxy_dir
    assert seen["backup_dir"] == settings.backup_dir
    assert seen["swag_name"] == "swag-test"
    assert audit_outcomes(settings, draft_id) == ["reload_requested"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_data_dir_fails_fast(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(0o555)
    try:
        with pytest.raises(ValueError, match=r"not writable by uid \d+"):
            create_app(make_settings(tmp_path, data_dir=data))
    finally:
        data.chmod(0o755)


def test_settings_read_swag_targets_from_env(monkeypatch) -> None:
    monkeypatch.setenv("SWAG_CONTAINER_NAME", "swag-test")
    monkeypatch.setenv("TAILSCALE_CONTAINER_NAME", "")
    monkeypatch.setenv("SWAG_UI_DATA_DIR", "/srv/data")
    monkeypatch.delenv("SWAG_UI_BACKUP_DIR", raising=False)
    monkeypatch.delenv("SWAG_CERT_DIR", raising=False)

    settings = Settings.from_env()

    assert settings.swag_container_name == "swag-test"
    assert settings.tailscale_container_name is None
    assert settings.backup_dir == Path("/srv/data/backups")
    assert settings.cert_dir == Path("/swag/letsencrypt/live")


def test_healthz_skips_host_check_but_api_does_not(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, proxy_dir=tmp_path, allowed_hosts=("swag-ui.example.com",))
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8080") as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/session").status_code == 400
        assert client.get("/healthz/").status_code == 400
    with TestClient(create_app(settings), base_url="http://swag-ui.example.com") as client:
        assert client.get("/api/session").status_code == 200

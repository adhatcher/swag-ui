from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.settings import Settings


def test_authenticated_draft_flow_and_path_denial(tmp_path: Path) -> None:
    proxy_dir = tmp_path / "proxy-confs"
    proxy_dir.mkdir()
    (proxy_dir / "one.subdomain.conf").write_text("server { listen 443; }\n")
    settings = Settings(
        proxy_dir=proxy_dir,
        data_dir=tmp_path / "data",
        helper_socket=tmp_path / "missing.sock",
        admin_password="very-long-test-password",
        session_secret="a" * 32,
        cookie_secure=False,
        allowed_hosts=("testserver",),
        litellm_url=None,
        litellm_key=None,
        litellm_model=None,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/proxies/one.subdomain.conf").status_code == 401
        anonymous = client.get("/api/session").json()
        token = anonymous["csrf_token"]
        assert (
            client.post("/api/login", json={"password": settings.admin_password}).status_code == 403
        )
        login = client.post(
            "/api/login",
            json={"password": settings.admin_password},
            headers={"X-CSRF-Token": token},
        )
        assert login.status_code == 200
        token = login.json()["csrf_token"]
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
            == 503
        )
        overview = client.get("/api/overview")
        assert overview.status_code == 200
        assert overview.json()["proxies"] == [{"name": proxy["name"], "active": None}]
        preview = client.post(
            "/api/ai/preview",
            json={"excerpt": "api_key=topsecret", "question": "token=questionsecret"},
            headers={"X-CSRF-Token": token},
        )
        assert preview.status_code == 200
        assert "topsecret" not in preview.json()["redacted_excerpt"]
        assert "questionsecret" not in preview.json()["redacted_question"]

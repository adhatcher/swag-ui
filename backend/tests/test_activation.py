import json
from pathlib import Path
from types import SimpleNamespace

from backend.app.files import read_proxy
from helper import control


class FakeContainer:
    def __init__(self, *, auto_reload: str = "false", test_exit: int = 0, reload_exit: int = 0):
        self.attrs = {"Config": {"Env": [f"SWAG_AUTORELOAD={auto_reload}"]}}
        self.test_exit = test_exit
        self.reload_exit = reload_exit
        self.commands: list[list[str]] = []

    def reload(self) -> None:
        pass

    def exec_run(self, args: list[str], demux: bool = False) -> SimpleNamespace:
        self.commands.append(args)
        if args == ["nginx", "-t"]:
            return SimpleNamespace(exit_code=self.test_exit, output=b"test failed")
        return SimpleNamespace(exit_code=self.reload_exit, output=b"reload failed")


class FakeClient:
    def __init__(self, container: FakeContainer):
        self.container = container
        self.containers = self

    def get(self, name: str) -> FakeContainer:
        assert name == control.SWAG_NAME
        return self.container


def setup_proxy(tmp_path: Path, monkeypatch):
    proxy_dir = tmp_path / "proxy-confs"
    proxy_dir.mkdir()
    live = proxy_dir / "one.subdomain.conf"
    live.write_text("server { listen 443; }\n")
    monkeypatch.setattr(control, "PROXY_DIR", proxy_dir)
    monkeypatch.setattr(control, "BACKUP_DIR", tmp_path / "backups")
    _, digest = read_proxy(proxy_dir, live.name)
    body = control.ActivationBody(
        name=live.name, content="server { listen 80; }\n", base_hash=digest
    )
    return live, body


def test_invalid_configuration_restores_without_reload(tmp_path: Path, monkeypatch) -> None:
    live, body = setup_proxy(tmp_path, monkeypatch)
    container = FakeContainer(test_exit=1)

    result = control.activate(body, FakeClient(container))

    assert result["status"] == "validation_failed"
    assert live.read_text() == "server { listen 443; }\n"
    assert container.commands == [["nginx", "-t"]]


def test_valid_configuration_requests_reload(tmp_path: Path, monkeypatch) -> None:
    live, body = setup_proxy(tmp_path, monkeypatch)
    container = FakeContainer()

    result = control.activate(body, FakeClient(container))

    assert result["status"] == "reload_requested"
    assert live.read_text() == body.content
    assert container.commands == [["nginx", "-t"], ["nginx", "-s", "reload"]]


def test_auto_reload_enabled_blocks_activation(tmp_path: Path, monkeypatch) -> None:
    live, body = setup_proxy(tmp_path, monkeypatch)
    container = FakeContainer(auto_reload="true")

    result = control.activate(body, FakeClient(container))

    assert result["status"] == "blocked"
    assert live.read_text() == "server { listen 443; }\n"
    assert container.commands == []


def test_unknown_auto_reload_blocks_activation(tmp_path: Path, monkeypatch) -> None:
    live, body = setup_proxy(tmp_path, monkeypatch)
    container = FakeContainer()
    container.attrs["Config"]["Env"] = []

    result = control.activate(body, FakeClient(container))

    assert result["status"] == "blocked"
    assert live.read_text() == "server { listen 443; }\n"


def test_reload_failure_restores_file_and_reports_uncertainty(tmp_path: Path, monkeypatch) -> None:
    live, body = setup_proxy(tmp_path, monkeypatch)
    container = FakeContainer(reload_exit=1)

    result = control.activate(body, FakeClient(container))

    assert result["status"] == "active_state_uncertain"
    assert live.read_text() == "server { listen 443; }\n"


def test_stale_revision_rejected(tmp_path: Path, monkeypatch) -> None:
    live, body = setup_proxy(tmp_path, monkeypatch)
    live.write_text("external edit\n")
    container = FakeContainer()

    result = control.activate(body, FakeClient(container))

    assert result["status"] == "conflict"
    assert live.read_text() == "external edit\n"


def test_status_returns_loaded_names_without_config_contents(tmp_path: Path, monkeypatch) -> None:
    container = FakeContainer()
    container.attrs["State"] = {"Status": "running"}

    def exec_run(args: list[str], demux: bool = False) -> SimpleNamespace:
        assert args == ["nginx", "-T"]
        return SimpleNamespace(
            exit_code=0,
            output=(
                b"# configuration file /config/nginx/proxy-confs/one.subdomain.conf:\n"
                b"proxy_set_header Authorization secret;\n"
            ),
        )

    container.exec_run = exec_run
    monkeypatch.setattr(control, "docker_client", lambda: FakeClient(container))
    monkeypatch.setattr(control, "CERT_DIR", tmp_path / "missing")
    monkeypatch.setattr(control, "TAILSCALE_NAME", None)

    result = control.status()

    assert result["swag"] == {"state": "running", "auto_reload": "false"}
    assert result["included_proxies"] == ["one.subdomain.conf"]
    assert "secret" not in str(result)


def test_certificate_metadata_ignores_invalid_files(tmp_path: Path) -> None:
    directory = tmp_path / "live" / "bad"
    directory.mkdir(parents=True)
    (directory / "cert.pem").write_text("not a certificate")
    assert control.certificate_metadata(tmp_path / "live") == []


def test_tailscale_status_parses_json_larger_than_log_tail(monkeypatch) -> None:
    container = FakeContainer()

    def exec_run(args: list[str], demux: bool = False) -> SimpleNamespace:
        assert args == ["tailscale", "status", "--json"]
        payload = {"Self": {"Online": True}, "Peer": {"details": "x" * 4000}}
        return SimpleNamespace(exit_code=0, output=json.dumps(payload).encode())

    container.exec_run = exec_run
    monkeypatch.setattr(control, "TAILSCALE_NAME", "tailscale")
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda name: container))
    assert control.tailscale_status(client) == {"state": "online"}

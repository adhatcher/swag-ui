import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app import swag
from backend.app.files import read_proxy

ORIGINAL = "server { listen 443; }\n"
CANDIDATE = "server { listen 80; }\n"


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
        assert name == "swag"
        return self.container


def setup_proxy(tmp_path: Path) -> tuple[Path, str, str, str]:
    proxy_dir = tmp_path / "proxy-confs"
    proxy_dir.mkdir()
    live = proxy_dir / "one.subdomain.conf"
    live.write_text(ORIGINAL)
    _, digest = read_proxy(proxy_dir, live.name)
    return live, live.name, CANDIDATE, digest


def run_activate(tmp_path: Path, live: Path, digest: str, container: FakeContainer) -> dict:
    return swag.activate(
        live.name,
        CANDIDATE,
        digest,
        proxy_dir=live.parent,
        backup_dir=tmp_path / "backups",
        swag_name="swag",
        client=FakeClient(container),
    )


def test_invalid_configuration_restores_without_reload(tmp_path: Path) -> None:
    live, _, _, digest = setup_proxy(tmp_path)
    container = FakeContainer(test_exit=1)

    result = run_activate(tmp_path, live, digest, container)

    assert result["status"] == "validation_failed"
    assert live.read_text() == ORIGINAL
    assert container.commands == [["nginx", "-t"]]


def test_valid_configuration_requests_reload(tmp_path: Path) -> None:
    live, _, _, digest = setup_proxy(tmp_path)
    container = FakeContainer()

    result = run_activate(tmp_path, live, digest, container)

    assert result["status"] == "reload_requested"
    assert live.read_text() == CANDIDATE
    assert container.commands == [["nginx", "-t"], ["nginx", "-s", "reload"]]


def test_auto_reload_enabled_blocks_activation(tmp_path: Path) -> None:
    live, _, _, digest = setup_proxy(tmp_path)
    container = FakeContainer(auto_reload="true")

    result = run_activate(tmp_path, live, digest, container)

    assert result["status"] == "blocked"
    assert live.read_text() == ORIGINAL
    assert container.commands == []


def test_unknown_auto_reload_blocks_activation(tmp_path: Path) -> None:
    live, _, _, digest = setup_proxy(tmp_path)
    container = FakeContainer()
    container.attrs["Config"]["Env"] = []

    result = run_activate(tmp_path, live, digest, container)

    assert result["status"] == "blocked"
    assert live.read_text() == ORIGINAL


def test_reload_failure_restores_file_and_reports_uncertainty(tmp_path: Path) -> None:
    live, _, _, digest = setup_proxy(tmp_path)
    container = FakeContainer(reload_exit=1)

    result = run_activate(tmp_path, live, digest, container)

    assert result["status"] == "active_state_uncertain"
    assert live.read_text() == ORIGINAL


def test_stale_revision_rejected(tmp_path: Path) -> None:
    live, _, _, digest = setup_proxy(tmp_path)
    live.write_text("external edit\n")
    container = FakeContainer()

    result = run_activate(tmp_path, live, digest, container)

    assert result["status"] == "conflict"
    assert live.read_text() == "external edit\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_proxy_dir_blocks_before_backup(tmp_path: Path) -> None:
    live, _, _, digest = setup_proxy(tmp_path)
    container = FakeContainer()
    live.parent.chmod(0o555)
    try:
        result = run_activate(tmp_path, live, digest, container)
    finally:
        live.parent.chmod(0o755)

    assert result["status"] == "blocked"
    assert "not writable" in result["message"]
    assert not (tmp_path / "backups").exists()
    assert live.read_text() == ORIGINAL
    assert container.commands == []


def test_foreign_owner_blocks_before_backup(tmp_path: Path, monkeypatch) -> None:
    live, _, _, digest = setup_proxy(tmp_path)
    owner = live.stat().st_uid
    monkeypatch.setattr(swag.os, "geteuid", lambda: owner + 1)
    container = FakeContainer()

    result = run_activate(tmp_path, live, digest, container)

    assert result["status"] == "blocked"
    assert f"uid {owner}" in result["message"]
    assert not (tmp_path / "backups").exists()
    assert live.read_text() == ORIGINAL
    assert container.commands == []


def test_status_returns_loaded_names_without_config_contents(tmp_path: Path) -> None:
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

    result = swag.status(
        swag_name="swag",
        tailscale_name=None,
        cert_dir=tmp_path / "missing",
        client=FakeClient(container),
    )

    assert result["swag"] == {"state": "running", "auto_reload": "false"}
    assert result["included_proxies"] == ["one.subdomain.conf"]
    assert "secret" not in str(result)


def test_status_without_docker_reports_unavailable(tmp_path: Path, monkeypatch) -> None:
    def no_docker():
        raise swag.docker.errors.DockerException("no socket")

    monkeypatch.setattr(swag, "docker_client", no_docker)

    result = swag.status(swag_name="swag", tailscale_name=None, cert_dir=tmp_path)

    assert result["swag"] == {"state": "unavailable", "auto_reload": "unknown"}
    assert result["included_proxies"] is None


def test_certificate_metadata_ignores_invalid_files(tmp_path: Path) -> None:
    directory = tmp_path / "live" / "bad"
    directory.mkdir(parents=True)
    (directory / "cert.pem").write_text("not a certificate")
    assert swag.certificate_metadata(tmp_path / "live") == []


def test_tailscale_status_parses_json_larger_than_log_tail() -> None:
    container = FakeContainer()

    def exec_run(args: list[str], demux: bool = False) -> SimpleNamespace:
        assert args == ["tailscale", "status", "--json"]
        payload = {"Self": {"Online": True}, "Peer": {"details": "x" * 4000}}
        return SimpleNamespace(exit_code=0, output=json.dumps(payload).encode())

    container.exec_run = exec_run
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda name: container))
    assert swag.tailscale_status(client, "tailscale") == {"state": "online"}


def test_docker_error_during_activation_restores_file(tmp_path: Path) -> None:
    live, _, _, digest = setup_proxy(tmp_path)
    container = FakeContainer()

    def exec_run(args: list[str], demux: bool = False) -> SimpleNamespace:
        container.commands.append(args)
        raise swag.docker.errors.APIError("socket closed")

    container.exec_run = exec_run

    result = run_activate(tmp_path, live, digest, container)

    assert result["status"] == "active_state_uncertain"
    assert live.read_text() == ORIGINAL
    assert container.commands == [["nginx", "-t"]]
    assert len(list((tmp_path / "backups").iterdir())) == 1

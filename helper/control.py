"""Fixed-operation control service, reachable only through a Unix socket."""

import json
import logging
import os
import re
import stat
import tempfile
import threading
import uuid
from pathlib import Path

import docker
from cryptography import x509
from fastapi import FastAPI
from pydantic import BaseModel

from backend.app.files import MAX_CONFIG_BYTES, InvalidProxyFile, read_proxy, valid_name

logger = logging.getLogger(__name__)

PROXY_DIR = Path(os.getenv("SWAG_PROXY_DIR", "/swag/proxy-confs"))
BACKUP_DIR = Path(os.getenv("SWAG_UI_BACKUP_DIR", "/data/backups"))
CERT_DIR = Path(os.getenv("SWAG_CERT_DIR", "/swag/letsencrypt/live"))
SWAG_NAME = os.getenv("SWAG_CONTAINER_NAME", "swag")
TAILSCALE_NAME = os.getenv("TAILSCALE_CONTAINER_NAME")
lock = threading.Lock()
app = FastAPI(title="SWAG control", docs_url=None, redoc_url=None)


class ActivationBody(BaseModel):
    name: str
    content: str
    base_hash: str


def docker_client():
    return docker.from_env()


def _container(client):
    return client.containers.get(SWAG_NAME)


def _auto_reload(container) -> str:
    container.reload()
    values = container.attrs.get("Config", {}).get("Env", [])
    matches = [
        value.partition("=")[2].lower() for value in values if value.startswith("SWAG_AUTORELOAD=")
    ]
    return matches[-1] if matches else "unknown"


def _exec(container, args: list[str], *, tail_limit: int | None = 3000) -> tuple[int, str]:
    result = container.exec_run(args, demux=False)
    output = (
        result.output.decode("utf-8", "replace")
        if isinstance(result.output, bytes)
        else str(result.output)
    )
    return result.exit_code, output[-tail_limit:] if tail_limit is not None else output


def _write_atomic(path: Path, content: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=".swag-ui-", dir=path.parent)
    try:
        original = os.stat(path, follow_symlinks=False)
        os.fchmod(fd, stat.S_IMODE(original.st_mode))
        os.fchown(fd, original.st_uid, original.st_gid)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def activate(body: ActivationBody, client=None) -> dict:
    try:
        valid_name(body.name)
    except InvalidProxyFile:
        return {"status": "rejected", "message": "Only direct .conf files can be activated"}
    candidate = body.content.encode("utf-8")
    if len(candidate) > MAX_CONFIG_BYTES:
        return {"status": "rejected", "message": "Draft exceeds size limit"}
    with lock:
        client = client or docker_client()
        try:
            container = _container(client)
            if _auto_reload(container) != "false":
                return {"status": "blocked", "message": "SWAG_AUTORELOAD must be explicitly false"}
            original, digest = read_proxy(PROXY_DIR, body.name)
        except (docker.errors.DockerException, OSError, InvalidProxyFile) as exc:
            logger.warning("Activation preflight failed: %s", type(exc).__name__)
            return {"status": "blocked", "message": "SWAG preflight unavailable"}
        if digest != body.base_hash:
            return {"status": "conflict", "message": "Proxy changed since draft was created"}
        path = PROXY_DIR / body.name
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(BACKUP_DIR, 0o700)
            backup = BACKUP_DIR / f"{uuid.uuid4().hex}-{body.name}"
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(original.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            logger.error("Backup failed: %s", type(exc).__name__)
            return {"status": "blocked", "message": "Could not secure a backup before activation"}
        try:
            _write_atomic(path, candidate)
            code, output = _exec(container, ["nginx", "-t"])
            if code != 0:
                _write_atomic(path, original.encode("utf-8"))
                return {"status": "validation_failed", "message": output}
            code, output = _exec(container, ["nginx", "-s", "reload"])
            if code != 0:
                _write_atomic(path, original.encode("utf-8"))
                return {
                    "status": "active_state_uncertain",
                    "message": (
                        "Reload failed; original file restored. Inspect SWAG before another apply."
                    ),
                }
            return {
                "status": "reload_requested",
                "message": (
                    "Nginx accepted the reload signal. Verify the service before relying "
                    "on the new route."
                ),
            }
        except (OSError, docker.errors.DockerException) as exc:
            logger.error("Activation failed: %s", type(exc).__name__)
            try:
                _write_atomic(path, original.encode("utf-8"))
            except OSError:
                logger.exception("Could not restore proxy file")
            return {
                "status": "active_state_uncertain",
                "message": "Activation failed; inspect the proxy file and SWAG state.",
            }


def certificate_metadata(cert_dir: Path) -> list[dict]:
    output = []
    if not cert_dir.is_dir():
        return output
    for directory in cert_dir.iterdir():
        if not directory.is_dir():
            continue
        certificate = directory / "cert.pem"
        try:
            cert = x509.load_pem_x509_certificate(certificate.read_bytes())
            expiration = cert.not_valid_after_utc.isoformat()
            output.append({"name": directory.name, "expires_at": expiration})
        except (OSError, ValueError):
            continue
    return sorted(output, key=lambda item: item["name"])


def tailscale_status(client) -> dict:
    if not TAILSCALE_NAME:
        return {"state": "unconfigured"}
    try:
        container = client.containers.get(TAILSCALE_NAME)
        code, output = _exec(container, ["tailscale", "status", "--json"], tail_limit=None)
        if code != 0:
            return {"state": "unavailable"}
        payload = json.loads(output)
        self_status = payload.get("Self", {})
        return {"state": "online" if self_status.get("Online") else "offline"}
    except (docker.errors.DockerException, ValueError, KeyError):
        return {"state": "unavailable"}


def included_proxy_names(container) -> list[str] | None:
    """Return only loaded proxy file names; discard Nginx's config dump."""
    result = container.exec_run(["nginx", "-T"], demux=False)
    if result.exit_code != 0 or not isinstance(result.output, bytes):
        return None
    names = set()
    pattern = re.compile(rb"^# configuration file /config/nginx/proxy-confs/([^/:]+\.conf):$")
    for line in result.output.splitlines():
        match = pattern.fullmatch(line)
        if match:
            name = match.group(1).decode("ascii", "ignore")
            try:
                names.add(valid_name(name))
            except InvalidProxyFile:
                pass
    return sorted(names)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/status")
def status() -> dict:
    try:
        client = docker_client()
        container = _container(client)
        container.reload()
        state = container.attrs.get("State", {}).get("Status", "unknown")
        auto_reload = _auto_reload(container)
        tailscale = tailscale_status(client)
        included = included_proxy_names(container)
    except docker.errors.DockerException:
        state, auto_reload, tailscale, included = (
            "unavailable",
            "unknown",
            {"state": "unavailable"},
            None,
        )
    return {
        "swag": {"state": state, "auto_reload": auto_reload},
        "tailscale": tailscale,
        "certificates": certificate_metadata(CERT_DIR),
        "included_proxies": included,
    }


@app.post("/activate")
def activate_route(body: ActivationBody) -> dict:
    return activate(body)

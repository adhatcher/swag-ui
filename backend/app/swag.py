"""Fixed SWAG operations: activate a proxy file and report SWAG status.

Every Docker call targets the configured SWAG (or Tailscale) container with a fixed
command; request input never shapes a command or a filesystem path.
"""

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

from backend.app.files import (
    MAX_CONFIG_BYTES,
    InvalidProxyFile,
    proxy_path,
    read_proxy,
    valid_name,
)

logger = logging.getLogger(__name__)

lock = threading.Lock()

# Seconds docker-py waits for each Docker API call (its default is 60).
DOCKER_TIMEOUT = 30


def docker_client():
    return docker.from_env(timeout=DOCKER_TIMEOUT)


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


def _writable_preflight(path: Path) -> str | None:
    """Explain why this process cannot safely replace ``path``, or return None."""
    euid = os.geteuid()
    if not os.access(path.parent, os.W_OK):
        return (
            f"Proxy folder is not writable by this container (uid {euid}); check the container user"
        )
    owner = os.stat(path, follow_symlinks=False).st_uid
    if euid != 0 and owner != euid:
        return (
            f"{path.name} is owned by uid {owner}, not this container's uid {euid}; "
            "fix its owner before applying"
        )
    return None


def _write_atomic(path: Path, content: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=".swag-ui-", dir=path.parent)
    try:
        original = os.stat(path, follow_symlinks=False)
        os.fchmod(fd, stat.S_IMODE(original.st_mode))
        if (original.st_uid, original.st_gid) != (os.geteuid(), os.getegid()):
            try:
                os.fchown(fd, original.st_uid, original.st_gid)
            except PermissionError:
                logger.warning("Could not keep the original owner of %s", path.name)
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


def activate(
    name: str,
    content: str,
    base_hash: str,
    *,
    proxy_dir: Path,
    backup_dir: Path,
    swag_name: str,
    client=None,
) -> dict:
    try:
        valid_name(name)
    except InvalidProxyFile:
        return {"status": "rejected", "message": "Only direct .conf files can be activated"}
    candidate = content.encode("utf-8")
    if len(candidate) > MAX_CONFIG_BYTES:
        return {"status": "rejected", "message": "Draft exceeds size limit"}
    with lock:
        try:
            client = client or docker_client()
            container = client.containers.get(swag_name)
            if _auto_reload(container) != "false":
                return {"status": "blocked", "message": "SWAG_AUTORELOAD must be explicitly false"}
            path = proxy_path(proxy_dir, name)
            original, digest = read_proxy(proxy_dir, path.name)
        except (docker.errors.DockerException, OSError, InvalidProxyFile) as exc:
            logger.warning("Activation preflight failed: %s", type(exc).__name__)
            return {"status": "blocked", "message": "SWAG preflight unavailable"}
        if digest != base_hash:
            return {"status": "conflict", "message": "Proxy changed since draft was created"}
        try:
            problem = _writable_preflight(path)
        except OSError as exc:
            logger.warning("Write preflight failed: %s", type(exc).__name__)
            problem = "Could not check write access to the proxy file"
        if problem:
            return {"status": "blocked", "message": problem}
        try:
            backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(backup_dir, 0o700)
            backup = backup_dir / f"{uuid.uuid4().hex}-{path.name}"
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
    try:
        directories = [entry for entry in cert_dir.iterdir() if entry.is_dir()]
    except OSError:
        return output
    for directory in directories:
        certificate = directory / "cert.pem"
        try:
            cert = x509.load_pem_x509_certificate(certificate.read_bytes())
        except PermissionError:
            # The certificate exists but this container's user cannot read it.
            logger.warning("Certificate for %s is not readable", directory.name)
            output.append({"name": directory.name, "expires_at": None})
            continue
        except (OSError, ValueError):
            continue
        output.append({"name": directory.name, "expires_at": cert.not_valid_after_utc.isoformat()})
    return sorted(output, key=lambda item: item["name"])


def tailscale_status(client, tailscale_name: str | None) -> dict:
    if not tailscale_name:
        return {"state": "unconfigured"}
    try:
        container = client.containers.get(tailscale_name)
        code, output = _exec(container, ["tailscale", "status", "--json"], tail_limit=None)
        if code != 0:
            return {"state": "unavailable"}
        payload = json.loads(output)
        self_status = payload.get("Self", {})
        return {"state": "online" if self_status.get("Online") else "offline"}
    except (docker.errors.DockerException, OSError, ValueError, KeyError):
        # docker-py raises requests' OSError subclasses for timeouts and lost connections.
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


def status(*, swag_name: str, tailscale_name: str | None, cert_dir: Path, client=None) -> dict:
    try:
        client = client or docker_client()
        container = client.containers.get(swag_name)
        container.reload()
        state = container.attrs.get("State", {}).get("Status", "unknown")
        auto_reload = _auto_reload(container)
        tailscale = tailscale_status(client, tailscale_name)
        included = included_proxy_names(container)
    except (docker.errors.DockerException, OSError):
        # docker-py raises requests' OSError subclasses for timeouts and lost connections.
        state, auto_reload, tailscale, included = (
            "unavailable",
            "unknown",
            {"state": "unavailable"},
            None,
        )
    return {
        "swag": {"state": state, "auto_reload": auto_reload},
        "tailscale": tailscale,
        "certificates": certificate_metadata(cert_dir),
        "included_proxies": included,
    }

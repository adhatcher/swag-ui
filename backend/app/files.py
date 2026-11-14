"""Read-only access to the allowlisted SWAG proxy directory."""

import hashlib
import os
import re
import stat
from pathlib import Path

MAX_CONFIG_BYTES = 1024 * 1024
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.conf$")


class InvalidProxyFile(ValueError):
    pass


def valid_name(name: str) -> str:
    if not NAME_PATTERN.fullmatch(name) or name in {".", ".."}:
        raise InvalidProxyFile("Only direct .conf files can be opened")
    return name


def read_proxy(proxy_dir: Path, name: str) -> tuple[str, str]:
    valid_name(name)
    fd = os.open(proxy_dir / name, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_CONFIG_BYTES:
            raise InvalidProxyFile("Proxy file is not a supported regular file")
        content = os.read(fd, MAX_CONFIG_BYTES + 1)
        if len(content) > MAX_CONFIG_BYTES:
            raise InvalidProxyFile("Proxy file exceeds size limit")
    finally:
        os.close(fd)
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidProxyFile("Proxy file is not UTF-8") from exc
    return decoded, hashlib.sha256(content).hexdigest()


def list_proxies(proxy_dir: Path) -> list[str]:
    names: list[str] = []
    for entry in proxy_dir.iterdir():
        if NAME_PATTERN.fullmatch(entry.name) and not entry.is_symlink() and entry.is_file():
            names.append(entry.name)
    return sorted(names)

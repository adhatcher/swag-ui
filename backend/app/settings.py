"""Configuration provided by the container runtime."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    proxy_dir: Path
    data_dir: Path
    helper_socket: Path
    admin_password: str
    session_secret: str
    cookie_secure: bool
    allowed_hosts: tuple[str, ...]
    litellm_url: str | None
    litellm_key: str | None
    litellm_model: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            proxy_dir=Path(os.getenv("SWAG_PROXY_DIR", "/swag/proxy-confs")),
            data_dir=Path(os.getenv("SWAG_UI_DATA_DIR", "/data")),
            helper_socket=Path(os.getenv("SWAG_HELPER_SOCKET", "/run/swag-ui/control.sock")),
            admin_password=os.getenv("SWAG_UI_ADMIN_PASSWORD", ""),
            session_secret=os.getenv("SWAG_UI_SESSION_SECRET", ""),
            cookie_secure=os.getenv("SWAG_UI_COOKIE_SECURE", "true").lower() == "true",
            allowed_hosts=tuple(
                host.strip()
                for host in os.getenv("SWAG_UI_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
                if host.strip()
            ),
            litellm_url=os.getenv("LITELLM_BASE_URL") or None,
            litellm_key=os.getenv("LITELLM_API_KEY") or None,
            litellm_model=os.getenv("LITELLM_MODEL") or None,
        )

    def validate(self) -> None:
        if len(self.admin_password) < 16:
            raise ValueError("SWAG_UI_ADMIN_PASSWORD must be at least 16 characters")
        if len(self.session_secret) < 32:
            raise ValueError("SWAG_UI_SESSION_SECRET must be at least 32 characters")
        if not self.allowed_hosts:
            raise ValueError("SWAG_UI_ALLOWED_HOSTS must contain at least one host")

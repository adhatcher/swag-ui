"""Client for the private, Unix-socket-only SWAG control service."""

from pathlib import Path

import httpx


class ControlUnavailable(RuntimeError):
    pass


class ControlTimeout(ControlUnavailable):
    """The helper did not answer in time; it may still be working on the request."""


STATUS_TIMEOUT = 25
# Activation runs `nginx -t` and a reload through the Docker API (docker-py waits up to 60s
# per call), so give it room before reporting an unknown outcome.
ACTIVATE_TIMEOUT = 150


class ControlClient:
    def __init__(self, socket: Path):
        self.socket = socket

    async def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        timeout: float = STATUS_TIMEOUT,
    ) -> dict:
        transport = httpx.AsyncHTTPTransport(uds=str(self.socket))
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://control", timeout=timeout
            ) as client:
                response = await client.request(method, path, json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.ReadTimeout as exc:
            # The request reached the helper, which may still finish it.
            raise ControlTimeout("SWAG control service did not answer in time") from exc
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise ControlUnavailable("SWAG control service is unavailable") from exc

    async def status(self) -> dict:
        return await self.request("GET", "/status")

    async def activate(self, name: str, content: str, base_hash: str) -> dict:
        return await self.request(
            "POST",
            "/activate",
            {"name": name, "content": content, "base_hash": base_hash},
            timeout=ACTIVATE_TIMEOUT,
        )

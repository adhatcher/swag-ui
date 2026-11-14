"""Client for the private, Unix-socket-only SWAG control service."""

from pathlib import Path

import httpx


class ControlUnavailable(RuntimeError):
    pass


class ControlClient:
    def __init__(self, socket: Path):
        self.socket = socket

    async def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        transport = httpx.AsyncHTTPTransport(uds=str(self.socket))
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://control", timeout=25
            ) as client:
                response = await client.request(method, path, json=payload)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise ControlUnavailable("SWAG control service is unavailable") from exc

    async def status(self) -> dict:
        return await self.request("GET", "/status")

    async def activate(self, name: str, content: str, base_hash: str) -> dict:
        return await self.request(
            "POST", "/activate", {"name": name, "content": content, "base_hash": base_hash}
        )

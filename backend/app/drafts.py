"""Drafts are stored outside SWAG's watched configuration tree."""

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path

from backend.app.files import MAX_CONFIG_BYTES, read_proxy, valid_name


@dataclass(frozen=True)
class Draft:
    id: str
    name: str
    content: str
    base_hash: str
    diff: str


class DraftStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS drafts (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, content TEXT NOT NULL,
                base_hash TEXT NOT NULL, diff TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY, draft_id TEXT NOT NULL, outcome TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path)) as conn:
            with conn:
                yield conn

    def create(self, proxy_dir: Path, name: str, content: str, base_hash: str) -> Draft:
        valid_name(name)
        if len(content.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise ValueError("Draft exceeds size limit")
        original, current_hash = read_proxy(proxy_dir, name)
        if current_hash != base_hash:
            raise ValueError("Proxy changed since it was opened")
        diff = "".join(
            unified_diff(
                original.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"live/{name}",
                tofile=f"draft/{name}",
            )
        )
        draft = Draft(str(uuid.uuid4()), name, content, base_hash, diff)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO drafts(id,name,content,base_hash,diff) VALUES (?,?,?,?,?)",
                (draft.id, draft.name, draft.content, draft.base_hash, draft.diff),
            )
        return draft

    def get(self, draft_id: str) -> Draft | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id,name,content,base_hash,diff FROM drafts WHERE id=?", (draft_id,)
            ).fetchone()
        return Draft(*row) if row else None

    def audit(self, draft_id: str, outcome: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO audit(draft_id,outcome) VALUES (?,?)", (draft_id, outcome))

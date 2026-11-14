from pathlib import Path

import pytest

from backend.app.drafts import DraftStore
from backend.app.files import InvalidProxyFile, read_proxy


def test_draft_stays_outside_live_swag_tree(tmp_path: Path) -> None:
    proxy_dir = tmp_path / "proxy-confs"
    proxy_dir.mkdir()
    live = proxy_dir / "one.subdomain.conf"
    live.write_text("server { listen 443; }\n")
    store = DraftStore(tmp_path / "app-data" / "drafts.sqlite3")
    _, base_hash = read_proxy(proxy_dir, live.name)

    draft = store.create(proxy_dir, live.name, "server { listen 80; }\n", base_hash)

    assert live.read_text() == "server { listen 443; }\n"
    assert "-server { listen 443; }" in draft.diff
    assert store.get(draft.id) == draft
    with pytest.raises(ValueError, match="changed"):
        store.create(proxy_dir, live.name, "different", "stale")


def test_proxy_access_rejects_traversal_and_symlink(tmp_path: Path) -> None:
    proxy_dir = tmp_path / "proxy-confs"
    proxy_dir.mkdir()
    secret = tmp_path / "secret.conf"
    secret.write_text("secret")
    (proxy_dir / "link.conf").symlink_to(secret)

    with pytest.raises(InvalidProxyFile):
        read_proxy(proxy_dir, "../secret.conf")
    with pytest.raises(OSError):
        read_proxy(proxy_dir, "link.conf")

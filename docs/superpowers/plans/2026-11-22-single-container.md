# Single Container Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the `web` and `helper` containers into one published image that writes proxy files and drives SWAG's nginx directly.

**Architecture:** `helper/control.py` becomes `backend/app/swag.py`, a plain module of functions (no FastAPI app) that takes its paths and container names as arguments. `main.py` calls those functions through `asyncio.to_thread`. The socket client, the helper package, and the helper Dockerfile stage are deleted. The one container runs as `99:100` with the proxy folder mounted read-write and the Docker socket's group added.

**Tech Stack:** Python 3.12, FastAPI, docker-py 7, pytest, uv; Docker / Compose; Unraid templates.

**Spec:** `docs/superpowers/specs/2026-11-21-single-container-design.md`

## Global Constraints

- Branch: `feat/single-container`, stacked on `fix/healthz-host-check`. Keep `HostCheckMiddleware` and its test unchanged.
- Container user: `99:100`. Never `root`, never `10001`.
- Mounts: `/swag/proxy-confs` (rw), `/swag/letsencrypt` (ro), `/var/run/docker.sock`, `/data`. Never SWAG's whole `/config`.
- Keep `read_only: true`, a `/tmp` tmpfs, `no-new-privileges:true` and `cap_drop: [ALL]` in Compose.
- Keep `--workers 1`; the module-level `threading.Lock` serialises activations.
- Backups default to `/data/backups`.
- Unchanged activation order: name and size check → autoreload preflight → hash conflict check → backup → atomic write → `nginx -t` (restore on failure) → `nginx -s reload` (restore on failure). The new writability preflight goes after the hash check and before the backup.
- Existing env var names stay: `SWAG_PROXY_DIR`, `SWAG_UI_DATA_DIR`, `SWAG_UI_BACKUP_DIR`, `SWAG_CERT_DIR`, `SWAG_CONTAINER_NAME`, `TAILSCALE_CONTAINER_NAME`.
- Coverage ≥ 80% (`make coverage`). `make check` passes.
- Never log draft contents or credentials, and never return them in `status()` output.

## Review Focus

1. **Existing `/data` owned by uid 10001 after upgrade.** Someone upgrading keeps an `app_data` volume created by the old image, so the new uid 99 can't write SQLite. Expected: the app exits at startup with a message that names the folder and the uid. Pinned by `test_unwritable_data_dir_fails_fast` (Task 2), with `chown` steps in the README migration (Task 4).
2. **A proxy file owned by another user** (for example `root`, after a shell edit). Expected: activation is `blocked` with a message naming the owner, and no backup or write happens. Pinned by `test_foreign_owner_blocks_before_backup` (Task 1).
3. **Proxy folder not writable** (wrong `--user`, or a read-only mount left over from the old template). Expected: `blocked`, nothing written. Pinned by `test_unwritable_proxy_dir_blocks_before_backup` (Task 1).
4. **Docker socket missing or not readable** (no `--group-add`). Expected: the overview shows SWAG `unavailable` and apply returns `blocked: SWAG preflight unavailable`, both with HTTP 200, not a 500. Pinned by `test_overview_and_apply_without_docker` (Task 2).
5. **`DOCKER_GID` unset in Compose.** Expected: `docker compose config` fails with a message telling the user to set it, rather than starting a container that can't reach Docker. Pinned by the Compose check in Task 3, Step 4.

---

### Task 1: `backend/app/swag.py` with a writability preflight

**Files:**
- Move: `helper/control.py` → `backend/app/swag.py` (`git mv`)
- Move: `backend/tests/test_activation.py` → `backend/tests/test_swag.py` (`git mv`)
- Delete: `helper/__init__.py`
- Modify: `Dockerfile` (drop the `COPY helper/ ./helper/` line only), `Makefile` (every `backend helper` → `backend`; `--cov=helper.control` removed)

**Interfaces:**
- Produces (all in `backend.app.swag`):
  - `activate(name: str, content: str, base_hash: str, *, proxy_dir: Path, backup_dir: Path, swag_name: str, client=None) -> dict`
  - `status(*, swag_name: str, tailscale_name: str | None, cert_dir: Path, client=None) -> dict`, with keys `swag`, `tailscale`, `certificates` and `included_proxies`, as today
  - `tailscale_status(client, tailscale_name: str | None) -> dict`
  - `certificate_metadata(cert_dir: Path) -> list[dict]`, `included_proxy_names(container) -> list[str] | None`, `docker_client()`
- Removed: `app`, `ActivationBody`, the module-level `PROXY_DIR`/`BACKUP_DIR`/`CERT_DIR`/`SWAG_NAME`/`TAILSCALE_NAME`, and the `/healthz`, `/status` and `/activate` routes.

- [ ] **Step 1: Move the files and port the existing tests**

`git mv` both files and `git rm helper/__init__.py`. In `test_swag.py`, import `from backend.app import swag`. `setup_proxy` returns `(live, name, content, digest)` instead of a body. Every call becomes `swag.activate(name, content, digest, proxy_dir=..., backup_dir=tmp_path / "backups", swag_name="swag", client=FakeClient(container))`. `FakeClient.get` asserts `name == "swag"`. The status test calls `swag.status(swag_name="swag", tailscale_name=None, cert_dir=tmp_path / "missing", client=FakeClient(container))`, and the Tailscale test calls `swag.tailscale_status(client, "tailscale")`. Drop all `monkeypatch.setattr(control, ...)` lines for removed globals.

- [ ] **Step 2: Add the new failing tests to `test_swag.py`**

```python
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_proxy_dir_blocks_before_backup(tmp_path, monkeypatch):
    live, name, content, digest = setup_proxy(tmp_path)
    live.parent.chmod(0o555)
    try:
        result = swag.activate(name, content, digest, proxy_dir=live.parent,
                               backup_dir=tmp_path / "backups", swag_name="swag",
                               client=FakeClient(FakeContainer()))
    finally:
        live.parent.chmod(0o755)
    assert result["status"] == "blocked"
    assert "not writable" in result["message"]
    assert not (tmp_path / "backups").exists()
    assert live.read_text() == "server { listen 443; }\n"

def test_foreign_owner_blocks_before_backup(tmp_path, monkeypatch):
    live, name, content, digest = setup_proxy(tmp_path)
    monkeypatch.setattr(swag.os, "geteuid", lambda: live.stat().st_uid + 1)
    container = FakeContainer()
    result = swag.activate(...same args..., client=FakeClient(container))
    assert result["status"] == "blocked"
    assert f"uid {live.stat().st_uid}" in result["message"]
    assert not (tmp_path / "backups").exists()
    assert container.commands == []

def test_status_without_docker_reports_unavailable(tmp_path, monkeypatch):
    def boom(): raise swag.docker.errors.DockerException("no socket")
    monkeypatch.setattr(swag, "docker_client", boom)
    result = swag.status(swag_name="swag", tailscale_name=None, cert_dir=tmp_path)
    assert result["swag"] == {"state": "unavailable", "auto_reload": "unknown"}
    assert result["included_proxies"] is None
```

- [ ] **Step 3: Run the tests and confirm the new ones fail**

Run: `uv run --frozen --extra dev python -m pytest backend/tests/test_swag.py -v`
Expected: the ported tests FAIL with `TypeError` (old signatures), and the two preflight tests FAIL.

- [ ] **Step 4: Refactor `swag.py`**

- Replace the globals with the keyword arguments above.
- Delete the FastAPI app, `ActivationBody` and the routes.
- Add `_writable_preflight(path: Path) -> str | None`. It returns `"Proxy folder is not writable by this container (uid {euid}); check the container user"` when `os.access(path.parent, os.W_OK)` is false. It returns `"{name} is owned by uid {st_uid}, not this container's uid {euid}; fix its owner before applying"` when `euid != 0` and the file's `st_uid != euid`. Otherwise it returns `None`. Use `os.geteuid()` through the `os` module so the test's monkeypatch applies.
- Call `_writable_preflight` right after the hash check. On a message, return `{"status": "blocked", "message": msg}`.
- In `_write_atomic`, call `os.fchown` only when `(st_uid, st_gid)` differs from `(os.geteuid(), os.getegid())`. If it raises `PermissionError`, log a warning naming only the file and continue. The preflight has already matched the uid, so only the group can differ.
- In `status`, pass `client or docker_client()` and keep catching `docker.errors.DockerException`.

- [ ] **Step 5: Update the Dockerfile and Makefile references, then run everything**

Remove `COPY helper/ ./helper/` from the Dockerfile. In the Makefile, change `backend helper` to `backend` in `lint`, `security` and `pr-check`, and change the coverage target to `--cov=backend.app`.
Run: `uv run --frozen --extra dev python -m pytest -v && make lint security`
Expected: all tests PASS. Lint and bandit are clean.

- [ ] **Step 6: Commit**

```bash
git add -A backend helper Dockerfile Makefile
git commit -m "Move SWAG control operations into the backend with a writability preflight"
```

---

### Task 2: Web app calls `swag` directly

**Files:**
- Modify: `backend/app/settings.py`, `backend/app/main.py`
- Delete: `backend/app/control_client.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `swag.activate(...)` and `swag.status(...)` from Task 1.
- Produces: `Settings` with `helper_socket` removed and these fields added after `data_dir`: `backup_dir: Path` (`SWAG_UI_BACKUP_DIR`, default `<data_dir>/backups`), `cert_dir: Path` (`SWAG_CERT_DIR`, default `/swag/letsencrypt/live`), `swag_container_name: str` (`SWAG_CONTAINER_NAME`, default `swag`), and `tailscale_container_name: str | None` (`TAILSCALE_CONTAINER_NAME`, empty → `None`).

- [ ] **Step 1: Rewrite `test_api.py` around a `make_settings` helper**

Add `make_settings(tmp_path, **overrides) -> Settings` with the current test values plus `backup_dir=tmp_path / "data" / "backups"`, `cert_dir=tmp_path / "certs"`, `swag_container_name="swag"`, `tailscale_container_name=None`. Use it in all three existing call sites. Delete `test_activation_timeout_reports_unknown_result`. Add a `no_docker` fixture that monkeypatches `backend.app.swag.docker_client` to raise `docker.errors.DockerException`, and use it in the draft-flow test. That test's apply assertion changes from `== 503` to `== 200`.

```python
def test_overview_and_apply_without_docker(tmp_path, no_docker):
    # login + create draft as in the flow test, then:
    apply = client.post(f"/api/drafts/{draft_id}/apply", json={}, headers=headers)
    assert apply.status_code == 200
    assert apply.json() == {"status": "blocked", "message": "SWAG preflight unavailable"}
    overview = client.get("/api/overview").json()
    assert overview["swag"] == {"state": "unavailable", "auto_reload": "unknown"}
    assert overview["proxies"] == [{"name": "one.subdomain.conf", "active": None}]
    # audit row for draft_id is ["blocked"]

def test_apply_passes_settings_to_swag(tmp_path, monkeypatch):
    seen = {}
    def fake_activate(name, content, base_hash, **kwargs):
        seen.update(kwargs, name=name)
        return {"status": "reload_requested", "message": "ok"}
    monkeypatch.setattr("backend.app.swag.activate", fake_activate)
    # login + draft + apply
    assert apply.json()["status"] == "reload_requested"
    assert seen["proxy_dir"] == settings.proxy_dir
    assert seen["backup_dir"] == settings.backup_dir
    assert seen["swag_name"] == "swag"

@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_data_dir_fails_fast(tmp_path):
    data = tmp_path / "data"; data.mkdir(); data.chmod(0o555)
    try:
        with pytest.raises(ValueError, match=r"not writable by uid \d+"):
            create_app(make_settings(tmp_path, data_dir=data))
    finally:
        data.chmod(0o755)

def test_settings_read_swag_targets_from_env(monkeypatch):
    monkeypatch.setenv("SWAG_CONTAINER_NAME", "swag-test")
    monkeypatch.setenv("TAILSCALE_CONTAINER_NAME", "")
    monkeypatch.setenv("SWAG_UI_DATA_DIR", "/srv/data")
    monkeypatch.delenv("SWAG_UI_BACKUP_DIR", raising=False)
    s = Settings.from_env()
    assert s.swag_container_name == "swag-test"
    assert s.tailscale_container_name is None
    assert s.backup_dir == Path("/srv/data/backups")
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run --frozen --extra dev python -m pytest backend/tests/test_api.py -v`
Expected: FAIL (`Settings` has no `backup_dir`; `main` still imports `control_client`).

- [ ] **Step 3: Update `settings.py` and `main.py`, and delete `control_client.py`**

- `Settings` gets the fields listed in Interfaces.
- `create_app`, after `settings.validate()`: create `data_dir` if it is missing. If `os.access(data_dir, os.W_OK)` is false, raise `ValueError(f"{data_dir} is not writable by uid {os.geteuid()}; chown it to the container user (99:100)")`.
- `overview` uses `await asyncio.to_thread(swag.status, swag_name=..., tailscale_name=..., cert_dir=...)`. Drop the `ControlUnavailable` fallback, since `status` already returns the unavailable shape.
- `apply_draft` uses `await asyncio.to_thread(swag.activate, draft.name, draft.content, draft.base_hash, proxy_dir=..., backup_dir=..., swag_name=...)`, then `store.audit(draft_id, result.get("status", "unknown"))`. Remove the 503 and 504 branches.
- Reference the functions as `swag.activate` and `swag.status` (with `from backend.app import swag`) so the monkeypatch in the tests applies.

- [ ] **Step 4: Run the full suite and coverage**

Run: `make lint test coverage security`
Expected: all PASS, coverage ≥ 80%.

- [ ] **Step 5: Commit**

```bash
git add -A backend
git commit -m "Call SWAG operations directly from the web app and drop the socket client"
```

---

### Task 3: One image, one Compose service, CI

**Files:**
- Modify: `Dockerfile`, `compose.yaml`, `.env.example`, `Makefile` (`build`), `.github/workflows/ci.yml`
- Delete: `scripts/helper-entrypoint.sh`

**Interfaces:**
- Produces: image default target = the `web` stage, which is the last stage. It runs as `99:100`, keeps the existing HEALTHCHECK and CMD, and has `/data` created and owned by `99:100`.

- [ ] **Step 1: Dockerfile**

Delete the `helper` stage. In `web`, replace the `groupadd`/`useradd` lines with `RUN mkdir -p /data && chown 99:100 /data` and set `USER 99:100`. Leave `/app` owned by root, since it's read-only at runtime.

- [ ] **Step 2: `compose.yaml` and `.env.example`**

Use one service, `swag-ui`, with:
- `build: .` and `image: swag-ui:local`
- `user: "99:100"` and `group_add: ["${DOCKER_GID:?set DOCKER_GID to the group id of /var/run/docker.sock (stat -c %g /var/run/docker.sock)}"]`
- The existing `ports` and `environment`, plus `SWAG_CONTAINER_NAME: ${SWAG_CONTAINER_NAME:-swag}` and `TAILSCALE_CONTAINER_NAME: ${TAILSCALE_CONTAINER_NAME:-}`
- Volumes: proxy-confs `:rw`, `etc/letsencrypt:/swag/letsencrypt:ro`, `/var/run/docker.sock:/var/run/docker.sock`, `app_data:/data`
- The existing hardening block

Remove `depends_on` and the `control_socket` and `backups` volumes. In `.env.example`, add `DOCKER_GID=` under `TAILSCALE_CONTAINER_NAME` with a comment line containing the `stat -c %g /var/run/docker.sock` instruction.

- [ ] **Step 3: Makefile `build`, CI, and the script**

Makefile `build` becomes the single line `docker build -t swag-ui:local .`. Delete the `helper-build` job from `ci.yml`. Run `git rm scripts/helper-entrypoint.sh`.

- [ ] **Step 4: Verify the Compose config**

Run: `SWAG_CONFIG_PATH=/tmp/x SWAG_UI_ADMIN_PASSWORD=x SWAG_UI_SESSION_SECRET=x docker compose config >/dev/null`
Expected: fails with `set DOCKER_GID ...`.
Run the same command with `DOCKER_GID=0` added.
Expected: exit 0, and the output shows one service.

- [ ] **Step 5: Build and smoke-test the image**

Run: `make build && docker run --rm -d --name swag-ui-smoke -e SWAG_UI_ADMIN_PASSWORD=$(openssl rand -hex 16) -e SWAG_UI_SESSION_SECRET=$(openssl rand -hex 32) swag-ui:local`, then wait about 35 s (the first healthcheck runs after the 30 s interval).
Expected: `docker inspect -f '{{.Config.User}} {{.State.Health.Status}}' swag-ui-smoke` prints `99:100 healthy`. Then run `docker rm -f swag-ui-smoke`.

- [ ] **Step 6: Commit**

```bash
git add -A Dockerfile compose.yaml .env.example Makefile .github scripts
git commit -m "Build and run SWAG UI as a single container"
```

---

### Task 4: README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite the container sections**

- The security paragraph (currently line 17) states plainly that the one container holds a read-write proxy mount and the Docker socket, that Docker socket access means root on the Unraid host if the app is compromised, and that it must stay LAN-only behind a long password.
- Replace **Step by step on Unraid** and **Using Unraid Docker templates** with a single Compose flow and one template table:
  - Repository `ghcr.io/adhatcher/swag-ui:latest`
  - Extra Parameters `--user 99:100 --group-add <gid>`
  - Paths: `/swag/proxy-confs` (rw), `/swag/letsencrypt` (ro), `/var/run/docker.sock`, `/data`
  - Variables: password, secret, allowed hosts, `SWAG_CONTAINER_NAME`, `TAILSCALE_CONTAINER_NAME`, and the LiteLLM ones
- Remove every mention of building the helper locally.
- Update the services table to one row, and put backups at `/data/backups`.
- In the troubleshooting table, delete the socket and helper rows. Add rows for `not writable by uid 99` (fix: `chown -R 99:100` on the folder), `is owned by uid N` (fix: `chown 99:100` on that proxy file), and SWAG `unavailable` (fix: check `--group-add` matches `stat -c %g /var/run/docker.sock`).
- In the activation paragraph, replace the "waits up to 150 seconds for the helper / result unknown" sentences: apply now runs to completion within the request, and a reverse proxy in front needs a read timeout above about 150 s.
- In the CI paragraph, say the full app image is published and drop "helper image is built locally".

- [ ] **Step 2: Add a "Migrating from two containers" section**

Steps, in order:
1. Stop and remove `swag-ui-helper` and delete its template.
2. Copy any wanted backups out of its `backups` volume or folder into the new `/data/backups`.
3. Run `chown -R 99:100` on the host folder or volume mapped to `/data`, because the old image wrote it as uid 10001.
4. Edit the `swag-ui` template: remove the `/run/swag-ui` mapping, change `/swag/proxy-confs` to read-write, and add the letsencrypt, docker.sock and Extra Parameters entries.
5. Apply, then confirm the overview shows SWAG **running** and auto reload **Disabled**.

For Compose users, the same `chown` applies to the `app_data` volume (`docker run --rm -v <project>_app_data:/data alpine chown -R 99:100 /data`).

- [ ] **Step 3: Check for leftovers**

Run: `grep -n -i "helper\|control.sock\|swag-ui-socket\|10001" README.md`
Expected: matches only inside the migration section.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers
git commit -m "Document the single-container deployment and migration"
```

---

### Final verification

- [ ] Run `make check coverage dependency-check` and `make build`. All must pass.
- [ ] Run `git grep -n -i "helper" -- ':!README.md' ':!docs'`. Expected: no matches.

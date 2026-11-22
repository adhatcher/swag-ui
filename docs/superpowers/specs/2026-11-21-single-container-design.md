# Single container: merge `web` and `helper`

Date: 2026-11-21 · Size: small change (one PR)

## Why

Splitting the app into two containers made it harder to run than it needs to be:

- The helper image is not published. It has to be built by hand on Unraid, and Unraid's update check does not cover it.
- The two Unraid templates have to agree on a shared socket folder (`/tmp/swag-ui-socket`), a socket group ID, and which one starts first. Much of the README's troubleshooting table covers these.
- The code carries an HTTP-over-Unix-socket layer: `ControlClient`, a 150 s timeout, an "activation result unknown" state, and a second uvicorn app.

Success: one published image, one Unraid template, the same features, and less code.

## Goals

- One image, published to GHCR by the existing workflow, deployable from a single Unraid template or a single Compose service.
- Keep every current behaviour: draft → diff → apply, the hash conflict check, the backup before writing, `nginx -t` with restore on failure, reload, active/inactive labels from `nginx -T`, the `SWAG_AUTORELOAD=false` preflight, certificate expiry, and Tailscale status.
- Delete the helper container, the socket client, and the second Dockerfile target.

## Non-goals

- New features or UI changes.
- Changing the activation steps or their safety checks.

## Anti-goals

- Mounting SWAG's whole `/config`. DNS credentials and private keys must stay out of the container.
- An activation path that writes a file without the preflight, backup and `nginx -t` steps.

## Approaches considered

1. **One container that runs as two users.** The entrypoint runs the helper as root on an in-container socket and the web app as an unprivileged user. Keeps the privilege boundary, but keeps the socket client and needs a supervisor. You turned this down as more complexity than it's worth.
2. **One process with the Docker socket. Chosen.** The web app writes proxy files through a read-write mount (the same mount SWAG uses) and runs the nginx commands in SWAG through the Docker socket. Simplest option, with every feature kept. **Accepted cost:** the LAN-facing process holds the Docker socket, so anyone who compromises the app has root on the Unraid host. The password, the CSRF check, the login rate limit and the LAN-only port binding are now the only things protecting it.
3. **No Docker; let SWAG reload itself** (`SWAG_AUTORELOAD=true`). Smallest attack surface, but loses the immediate `nginx -t` result and the automatic restore, the active/inactive labels, and Tailscale status. A broken file would stay on disk and stop nginx at SWAG's next restart. Rejected.

## Design

**Code**
- Move `helper/control.py` into the backend as `backend/app/swag.py` (operations only, with no FastAPI app): `activate`, `status`, `certificate_metadata`, `tailscale_status`, `included_proxy_names`, `_write_atomic`. Paths and container names come from `Settings`, not module-level `os.getenv`.
- `main.py` calls these directly and runs the blocking Docker calls with `asyncio.to_thread`. The module-level `threading.Lock` still serialises activations (`--workers 1` stays).
- Delete `backend/app/control_client.py`, `helper/`, `scripts/helper-entrypoint.sh`, `Settings.helper_socket`, `ControlTimeout`/`ControlUnavailable`, and the `result_unknown` 504 path. If Docker is unreachable, the overview reports `unavailable` as it does now, and apply returns the existing `blocked` result.
- `Settings` gains `swag_container_name`, `tailscale_container_name`, `cert_dir` and `backup_dir` (default `/data/backups`), all read from the existing env var names.

**Writing as SWAG's user**
- The container runs as `99:100` (Unraid's `nobody:users`, which owns SWAG's files), so atomic replace needs no root. The image's default `USER` changes from `10001` to `99:100`.
- `_write_atomic` skips `fchown` when the owner already matches. Activation checks that the proxy directory is writable **before** taking the backup and returns `blocked` with a clear message if it is not, so it never fails halfway through.

**Container**
- One Dockerfile target (the final stage), so the shared publish workflow publishes the full app with no workflow change.
- Mounts:
  - `${SWAG_CONFIG_PATH}/nginx/proxy-confs` → `/swag/proxy-confs` (rw)
  - `${SWAG_CONFIG_PATH}/etc/letsencrypt` → `/swag/letsencrypt` (ro)
  - `/var/run/docker.sock` → `/var/run/docker.sock`
  - app data → `/data` (drafts, audit, backups)
- Run with `--user 99:100 --group-add <docker.sock gid>`. In Compose: `user:` and `group_add: ["${DOCKER_GID}"]`.
- Keep `read_only: true`, the `/tmp` tmpfs, `no-new-privileges` and `cap_drop: ALL`. None of them are needed by the new design.
- The HTTP healthcheck on `/healthz` stays. This branch is stacked on `fix/healthz-host-check` so it includes that fix.

**Build, CI, docs**
- Makefile: one `docker build`; remove `helper` from the lint, coverage and bandit paths.
- CI: remove the `helper-build` job.
- `compose.yaml`: one service; drop the `control_socket` and `backups` volumes.
- `.env.example`: add `DOCKER_GID`.
- README: replace the two-container sections with one template table and one Compose flow. Rewrite the security paragraph to state the accepted Docker-socket risk plainly. Add a short **Migrating from two containers** section: remove `swag-ui-helper` and its template, copy any wanted backups out of its `backups` volume, drop the `/tmp/swag-ui-socket` mapping, and add the new mounts, variables and extra parameters to the `swag-ui` template. Trim the troubleshooting rows that only applied to the socket.
- Keep the note about reverse-proxy read timeouts. A slow `nginx -t` or reload now just makes the HTTP request take longer.

**Tests**
- `test_activation.py` → `test_swag.py`, aimed at the new module with the same fake Docker client.
- Replace the timeout test in `test_api.py` with tests that monkeypatch the `swag` functions: apply success, the Docker-unavailable overview, and a `blocked` result.
- New test: an unwritable proxy directory gives `blocked`, and no backup or file write happens.
- Coverage stays at or above 80%.

## Left to the builder

- Whether `swag.py` takes `Settings` or explicit arguments.
- Exact README wording and table layout.
- How the Unraid docker group ID is shown to the user (`stat -c %g /var/run/docker.sock` on the host is the expected instruction).

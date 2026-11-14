# SWAG Control

SWAG Control is a private web interface for reviewing and editing [LinuxServer SWAG](https://docs.linuxserver.io/images/docker-swag/) proxy configuration on Unraid. Edits are saved as drafts, reviewed as diffs, then validated by SWAG's own `nginx -t` before a reload is requested.

## What it manages

- Lists direct `.conf` files in SWAG's `nginx/proxy-confs` directory. A file's active status is shown as **unknown** until the Nginx include graph is verified; the extension alone is not proof that a file is loaded.
- Reads and edits a selected proxy file with revision conflict detection and persistent drafts.
- Activates a draft through a separate control container. Validation failures restore the original file without requesting a reload.
- Shows SWAG state, the `SWAG_AUTORELOAD` setting, certificate names and expiry dates, and optional Tailscale container status.
- Sends a user-approved, redacted excerpt to an optional LiteLLM gateway and displays advice. AI output does not write files.

DNS credentials, private keys, certificate contents, sample proxy files, site configuration, and Unraid's Docker template are outside the editor's allowlist. Certificate and Tailscale settings are currently read-only.

## Architecture and access

The `web` container mounts proxy files **read-only**. Drafts and audit outcomes live in its `app_data` volume. The `helper` container is the sole live writer: it has a writable proxy mount, a backup volume, and Docker socket access so it can run fixed `nginx -t` and `nginx -s reload` commands against the configured SWAG container. It listens only on a shared Unix socket; it has no TCP listener or network interface. Docker daemon access gives the helper broad host authority, so deploy this stack only on a trusted Unraid host and keep its socket private.

The app has a local admin password, signed session cookies, CSRF checks, and a login rate limit. Serve it over HTTPS on a private network such as Tailscale. The default Compose port binds to `127.0.0.1`, so it is not reachable from other machines until you deliberately choose a private bind address or trusted HTTPS proxy. Never expose the admin UI directly to the public internet.

## Unraid deployment

1. Confirm the **host** path mapped to `/config` in the SWAG container. Set `SWAG_CONFIG_PATH` to that path. `/mnt/user/appdata/swag` in `.env.example` is only an example; a network mount of the share on another machine is not the Unraid host path.
2. In SWAG's Unraid Docker template, set `SWAG_AUTORELOAD=false` explicitly and recreate the SWAG container. The helper checks this setting **before every activation** and blocks activation if it is true or unknown. Draft editing still works when activation is blocked. SWAG's [auto reload setting](https://docs.linuxserver.io/images/docker-swag/) can otherwise load a changed file before validation completes.
3. Copy `.env.example` to `.env`. Set a random admin password of at least 16 characters and a random session secret of at least 32 characters. Set `SWAG_UI_ALLOWED_HOSTS` to the hostname you will use, and keep `SWAG_UI_COOKIE_SECURE=true` for HTTPS access. Do not commit `.env`.
4. Set `SWAG_CONTAINER_NAME` to the exact Docker container name. If Tailscale runs in a container with the CLI, optionally set `TAILSCALE_CONTAINER_NAME`; host-installed Tailscale currently reports `unconfigured`.
5. To use AI advice, set `LITELLM_BASE_URL` to the gateway's OpenAI-compatible `/v1` base URL, plus `LITELLM_MODEL` and, if required, `LITELLM_API_KEY`. The web container needs network reachability to that gateway. Selected text may contain private hostnames; review the redacted preview before sending.
6. Build and start the two services with `docker compose --env-file .env up -d --build`. Map the host port to a private interface or place the app behind a trusted HTTPS reverse proxy. Avoid making this management interface depend on the SWAG instance it controls for emergency access.
7. Before enabling activation against production, test with a disposable SWAG container using the same image and representative proxy files. Confirm valid, invalid, and conflicting edits, and inspect the retained backup and SWAG logs.

Ports and volumes:

| Service | Exposure | Persistent or host mounts |
| --- | --- | --- |
| `web` | Host `${SWAG_UI_BIND_ADDRESS}:${SWAG_UI_PORT}` to container `8080` | `app_data` for drafts/audit; proxy directory read-only; private control socket |
| `helper` | No TCP/network exposure | Proxy directory writable; Let’s Encrypt metadata read-only; Docker socket; `backups`; private control socket |

The helper's `/backups` volume retains a copy of the original file for each activation. Docker logs show service health and errors, without logging credentials or draft contents. The `web` image has an HTTP health check and the helper checks its Unix socket.

## Activation and recovery

Activation checks the current file hash, backs up the original outside SWAG's watched tree, atomically replaces the file, runs `nginx -t`, then asks Nginx to reload. A failed syntax test restores the file without a reload. A failed reload restores the file and reports **active state uncertain** because the running configuration cannot be inferred from a failed signal alone. A successful reload command is reported as **reload requested**, not as proof that every route works.

If active state is uncertain, stop applying drafts. Inspect the SWAG container logs and running routes, compare the current file with the backup in the helper's `backups` volume, restore the original if needed, run `nginx -t` in SWAG, and manually reload or restart only after the original configuration is known good. External edits or manual reloads are outside the helper's lock; the hash check detects a changed file before activation but cannot prevent another administrator from acting simultaneously.

## Development and PR checks

Requirements: Python 3.12+, [`uv`](https://docs.astral.sh/uv/), Node 22, npm 12, and Docker for image builds.

```sh
make install
make lint
make test
make coverage
make security
make dependency-check
make pr-check
make build
```

For local API development, set `SWAG_UI_ADMIN_PASSWORD`, `SWAG_UI_SESSION_SECRET`, `SWAG_UI_COOKIE_SECURE=false`, `SWAG_UI_ALLOWED_HOSTS=localhost,127.0.0.1`, `SWAG_PROXY_DIR` to a disposable proxy directory, and `SWAG_UI_DATA_DIR` to a writable disposable directory. Run `uv run uvicorn backend.app.main:create_app --factory --reload`. Run `npm run dev` from `frontend/` to use Vite; its dev server proxies `/api` to the API.

GitHub Actions uses the same pinned shared workflow family as Bourbon Book: CI checks and builds of both container targets on pull requests, CodeQL for Actions/TypeScript/Python, and GHCR publication of the web image only after successful CI on `main`. The helper image is built locally by Compose. GitHub execution is verified when the first PR runs; local checks do not establish the remote result.

# SWAG Control

SWAG Control is a private web interface for reviewing and editing [LinuxServer SWAG](https://docs.linuxserver.io/images/docker-swag/) proxy configuration on Unraid. Edits are saved as drafts, reviewed as diffs, then validated by SWAG's own `nginx -t` before a reload is requested.

## What it manages

- Lists direct `.conf` files in SWAG's `nginx/proxy-confs` directory. A file is shown as **active** when SWAG's `nginx -T` output lists it as loaded, **inactive** when it does not, and **unknown** when the helper or SWAG is unavailable; the `.conf` extension alone is not proof that a file is loaded.
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

### Step by step on Unraid

The app is two containers that share a private socket: the published `ghcr.io/adhatcher/swag-ui` image is only the `web` half. On its own it cannot activate drafts, and it exits at startup unless the admin password and session secret are set. The `helper` image is not published, so it is built on the host. You can deploy with Docker Compose from the Unraid terminal (below), or with two Unraid Docker templates (see [Using Unraid Docker templates](#using-unraid-docker-templates)). A single template for the `web` image is not enough.

Run every command in this guide on the Unraid server, in its web terminal (the `>_` icon) or over SSH, not on another machine with the share mounted.

1. If you already created a `swag-ui` container from a template, remove it so it does not hold the port: `docker rm -f swag-ui`. Delete the template from the Docker tab as well.
2. Check that Compose is available with `docker compose version`. If it is not, install the **Compose Manager** plugin from Community Applications.
3. Download the code into an appdata folder. Unraid may not include `git`, so fetch the archive:

   ```sh
   mkdir -p /mnt/user/appdata/swag-ui && cd /mnt/user/appdata/swag-ui
   wget -qO- https://github.com/adhatcher/swag-ui/archive/refs/heads/main.tar.gz | tar xz --strip-components=1
   ```

   Any appdata location works. The Compose project takes its name from the folder, so the volumes in this guide are named `swag-ui_*`.
4. Find SWAG's container name and the host folder mapped to its `/config`:

   ```sh
   docker ps --format '{{.Names}}' | xargs -I{} docker inspect {} --format '{{slice .Name 1}} {{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}'
   ```

   Each line shows a container name and, for containers with a `/config` mount, its host folder. Use SWAG's name for `SWAG_CONTAINER_NAME` and its folder for `SWAG_CONFIG_PATH`; that folder must contain `nginx/proxy-confs`.
5. In SWAG's Unraid template, add the variable `SWAG_AUTORELOAD` with the value `false`, then apply so the container is recreated.
6. Create `.env`. Replace the example IP, hostname, path and container name with your own:

   ```sh
   cd /mnt/user/appdata/swag-ui && cat > .env <<EOF
   SWAG_CONFIG_PATH=/mnt/user/appdata/swag
   SWAG_CONTAINER_NAME=swag
   SWAG_UI_BIND_ADDRESS=192.168.1.10
   SWAG_UI_PORT=8022
   SWAG_UI_ALLOWED_HOSTS=192.168.1.10,tower.local
   SWAG_UI_COOKIE_SECURE=false
   SWAG_UI_ADMIN_PASSWORD=$(openssl rand -base64 18)
   SWAG_UI_SESSION_SECRET=$(openssl rand -hex 32)
   EOF
   chmod 600 .env && grep ADMIN_PASSWORD .env
   ```

   The last command prints the sign-in password.
   - `SWAG_UI_BIND_ADDRESS` is the host IP the UI listens on. Use the server's LAN IP, or its Tailscale IP to keep the UI on your tailnet. Never forward this port from the internet.
   - `SWAG_UI_ALLOWED_HOSTS` lists every hostname or IP you will type in the browser, without the port.
   - `SWAG_UI_COOKIE_SECURE=false` is only for plain HTTP on a private network. Set it to `true` when the UI is served over HTTPS.
   - Optionally add `TAILSCALE_CONTAINER_NAME` and the `LITELLM_*` values from `.env.example`.
7. Build and start both containers:

   ```sh
   cd /mnt/user/appdata/swag-ui && docker compose --env-file .env up -d --build && docker compose ps
   ```

   Both `web` and `helper` should report `healthy` within about a minute.
8. Open `http://<bind-address>:8022` and sign in. The overview should show SWAG as **running** and auto reload as **Disabled**. Make one harmless change, such as adding a comment line to a proxy file, and activate it. A green notice should say "Nginx accepted the reload signal. Verify the service before relying on the new route.", and `docker logs <swag container>` should show no errors.

To update later, repeat the download in step 3; it keeps your `.env`. Then run the command in step 7 again. To stop the app, run `docker compose down` in the same folder. That keeps the drafts and backups volumes; `docker compose down -v` deletes them.

### Using Unraid Docker templates

Use this instead of Compose if you prefer to manage containers from the Docker tab. You create two containers: `swag-ui-helper` and `swag-ui`. They share a socket through a host folder. Replace the example IP, hostname and paths with your own; find SWAG's container name and `/config` folder with step 4 above.

1. In SWAG's template, add the variable `SWAG_AUTORELOAD` with the value `false` and apply.
2. Build the helper image on the server. It is not published, so it exists only on this host:

   ```sh
   mkdir -p /mnt/user/appdata/swag-ui/src && cd /mnt/user/appdata/swag-ui/src
   wget -qO- https://github.com/adhatcher/swag-ui/archive/refs/heads/main.tar.gz | tar xz --strip-components=1
   docker build --target helper -t swag-ui-helper:local .
   ```

3. Create the web container's data folder. The web container runs as uid 10001, which must own it:

   ```sh
   mkdir -p /mnt/user/appdata/swag-ui/data && chown 10001:10001 /mnt/user/appdata/swag-ui/data
   ```

4. Generate the admin password and session secret, and keep the output for step 6. The first line is the sign-in password:

   ```sh
   openssl rand -base64 18; openssl rand -hex 32
   ```

5. In the Docker tab, click **Add Container** and create the helper:

   | Field | Value |
   | --- | --- |
   | Name | `swag-ui-helper` |
   | Repository | `swag-ui-helper:local` |
   | Network Type | `None` |
   | Variable `SWAG_CONTAINER_NAME` | SWAG's container name, for example `swag` |
   | Variable `SWAG_UI_SOCKET_GID` | `10001` |
   | Variable `TAILSCALE_CONTAINER_NAME` | Optional. A Tailscale container with the CLI |
   | Path `/swag/proxy-confs` | `/mnt/user/appdata/swag/nginx/proxy-confs`, read/write |
   | Path `/swag/letsencrypt` | `/mnt/user/appdata/swag/etc/letsencrypt`, read only |
   | Path `/var/run/docker.sock` | `/var/run/docker.sock` |
   | Path `/run/swag-ui` | `/tmp/swag-ui-socket` |
   | Path `/backups` | `/mnt/user/appdata/swag-ui/backups` |
   | Extra Parameters | `--security-opt no-new-privileges:true` |

6. Click **Add Container** again and create the web UI:

   | Field | Value |
   | --- | --- |
   | Name | `swag-ui` |
   | Repository | `ghcr.io/adhatcher/swag-ui:latest` |
   | Network Type | `Bridge` |
   | Port | Host `8022`, container `8080` |
   | Variable `SWAG_UI_ADMIN_PASSWORD` | First line from step 4 (at least 16 characters) |
   | Variable `SWAG_UI_SESSION_SECRET` | Second line from step 4 (at least 32 characters) |
   | Variable `SWAG_UI_ALLOWED_HOSTS` | Every hostname or IP you type in the browser, for example `192.168.1.10,tower.local` |
   | Variable `SWAG_UI_COOKIE_SECURE` | `false` for plain HTTP on a private network, `true` behind HTTPS |
   | Variable `LITELLM_BASE_URL`, `LITELLM_MODEL`, `LITELLM_API_KEY` | Optional, for AI advice |
   | Path `/swag/proxy-confs` | `/mnt/user/appdata/swag/nginx/proxy-confs`, read only |
   | Path `/data` | `/mnt/user/appdata/swag-ui/data` |
   | Path `/run/swag-ui` | `/tmp/swag-ui-socket` |
   | Extra Parameters | `--read-only --tmpfs /tmp --security-opt no-new-privileges:true --cap-drop ALL` |

   Do not map `/config`; the app does not use it.
7. Start `swag-ui-helper`, then `swag-ui`. Open `http://<server-ip>:8022` and sign in with the password. There is no username. The overview should show SWAG as **running** and auto reload as **Disabled**; then test one harmless activation as in step 8 above.

Notes:

- Both containers must map the same host folder to `/run/swag-ui`. `/tmp` on Unraid is cleared at reboot; the helper recreates the socket when it starts.
- Unraid's update check works for `swag-ui`. It does not apply to `swag-ui-helper:local`: to update the helper, repeat the download and build in step 2, then restart the container.
- Never forward the UI port from the internet.

### Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `docker: invalid reference format` when applying a template | The template's Repository field holds a command such as `docker pull ghcr.io/...`. It must hold only an image name, such as `ghcr.io/adhatcher/swag-ui:latest`. |
| `ValueError: SWAG_UI_ADMIN_PASSWORD must be at least 16 characters` (or `SWAG_UI_SESSION_SECRET ... 32`) in the logs | The container started without those settings, or with values that are too short. With Compose, set both in `.env` and start with `docker compose --env-file .env`. With templates, add both variables to the `swag-ui` template as in step 6 of the template steps. |
| Overview shows SWAG `unavailable` with templates | The two containers do not share the socket folder, or the helper is not running. Check that both map the same host folder to `/run/swag-ui`, and run `docker logs swag-ui-helper`. |
| Auto reload shows **Unknown** | SWAG's container has no `SWAG_AUTORELOAD` variable. Add it with the value `false` and apply; activation is blocked until then. |
| `Invalid host header` in the browser | The hostname or IP in the address bar is missing from `SWAG_UI_ALLOWED_HOSTS`. Add it and run `docker compose --env-file .env up -d`. |
| Sign-in fails with `Invalid CSRF token` over plain HTTP | `SWAG_UI_COOKIE_SECURE=true` makes the browser discard the session cookie on an `http://` address. Use HTTPS, or set it to `false` on a private network. |
| `Bind for ... failed: port is already allocated` | Another container already uses the port, often a leftover template container. Remove it or change `SWAG_UI_PORT`. |
| Overview shows SWAG `unavailable` | The helper is not running or `SWAG_CONTAINER_NAME` is wrong. Check `docker compose logs helper` and `docker ps`. |
| Activation says `SWAG_AUTORELOAD must be explicitly false` | SWAG's container does not have `SWAG_AUTORELOAD=false`. Set it in SWAG's template and apply. |
| No proxy files listed | `SWAG_CONFIG_PATH` does not point at the folder containing `nginx/proxy-confs`, or the files are not readable by others. The web container reads them as uid 10001. |

Ports and volumes:

| Service | Exposure | Persistent or host mounts |
| --- | --- | --- |
| `web` | Host `${SWAG_UI_BIND_ADDRESS}:${SWAG_UI_PORT}` to container `8080` | `app_data` for drafts/audit; proxy directory read-only; private control socket |
| `helper` | No TCP/network exposure | Proxy directory writable; Let’s Encrypt metadata read-only; Docker socket; `backups`; private control socket |

The helper's `/backups` volume retains a copy of the original file for each activation. Docker logs show service health and errors, without logging credentials or draft contents. The `web` image has an HTTP health check and the helper checks its Unix socket.

## Activation and recovery

Activation checks the current file hash, backs up the original outside SWAG's watched tree, atomically replaces the file, runs `nginx -t`, then asks Nginx to reload. A failed syntax test restores the file without a reload. A failed reload restores the file and reports **active state uncertain** because the running configuration cannot be inferred from a failed signal alone. A successful reload command is reported as **reload requested**, not as proof that every route works. The web service waits up to 150 seconds for the helper; if it gets no answer it reports **activation result unknown**, because the helper may still finish under its lock. If you put a reverse proxy in front of the UI, give it a read timeout above 150 seconds.

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

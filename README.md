# SWAG Control

SWAG Control is a private web interface for reviewing and editing [LinuxServer SWAG](https://docs.linuxserver.io/images/docker-swag/) proxy configuration on Unraid. Edits are saved as drafts, reviewed as diffs, then validated by SWAG's own `nginx -t` before a reload is requested.

## What it manages

- Lists direct `.conf` files in SWAG's `nginx/proxy-confs` directory. A file is shown as **active** when SWAG's `nginx -T` output lists it as loaded, **inactive** when it does not, and **unknown** when Docker or SWAG is unavailable; the `.conf` extension alone is not proof that a file is loaded.
- Reads and edits a selected proxy file with revision conflict detection and persistent drafts.
- Activates a draft by writing the file and running SWAG's own `nginx -t` and reload. Validation failures restore the original file without requesting a reload.
- Shows SWAG state, the `SWAG_AUTORELOAD` setting, certificate names and expiry dates, and optional Tailscale container status.
- Sends a user-approved, redacted excerpt to an optional LiteLLM gateway and displays advice. AI output does not write files.

DNS credentials, private keys, certificate contents, sample proxy files, site configuration, and Unraid's Docker template are outside the editor's allowlist. Certificate and Tailscale settings are currently read-only.

## Architecture and access

SWAG Control is one container. It runs as `99:100` (Unraid's `nobody:users`, which owns SWAG's files) and mounts SWAG's `nginx/proxy-confs` folder **read-write**, the same folder SWAG itself uses, plus SWAG's Let's Encrypt folder read-only for certificate dates. It also has the Docker socket, so it can run fixed `nginx -t`, `nginx -s reload` and `nginx -T` commands in the configured SWAG container and read its settings. Drafts, the audit log and pre-activation backups live in `/data`.

**Docker socket access is root on the host.** Anyone who takes over this app can control every container on the Unraid server. Its password, CSRF checks and login rate limit are the only things in the way, so use a long random password, keep the UI on your LAN or tailnet, and deploy it only on a trusted host.

The app has a local admin password, signed session cookies, CSRF checks, and a login rate limit. Serve it over HTTPS on a private network such as Tailscale. The default Compose port binds to `127.0.0.1`, so it is not reachable from other machines until you deliberately choose a private bind address or trusted HTTPS proxy. Never expose the admin UI directly to the public internet.

## Unraid deployment

1. Confirm the **host** path mapped to `/config` in the SWAG container. Set `SWAG_CONFIG_PATH` to that path. `/mnt/user/appdata/swag` in `.env.example` is only an example; a network mount of the share on another machine is not the Unraid host path.
2. In SWAG's Unraid Docker template, set `SWAG_AUTORELOAD=false` explicitly and recreate the SWAG container. The app checks this setting **before every activation** and blocks activation if it is true or unknown. Draft editing still works when activation is blocked. SWAG's [auto reload setting](https://docs.linuxserver.io/images/docker-swag/) can otherwise load a changed file before validation completes.
3. Copy `.env.example` to `.env`. Set a random admin password of at least 16 characters and a random session secret of at least 32 characters. Set `SWAG_UI_ALLOWED_HOSTS` to the hostname you will use, and keep `SWAG_UI_COOKIE_SECURE=true` for HTTPS access. Set `DOCKER_GID` to the output of `stat -c %g /var/run/docker.sock`. Do not commit `.env`.
4. Set `SWAG_CONTAINER_NAME` to the exact Docker container name. If Tailscale runs in a container with the CLI, optionally set `TAILSCALE_CONTAINER_NAME`; host-installed Tailscale currently reports `unconfigured`.
5. To use AI advice, set `LITELLM_BASE_URL` to the gateway's OpenAI-compatible `/v1` base URL, plus `LITELLM_MODEL` and, if required, `LITELLM_API_KEY`. The container needs network reachability to that gateway. Selected text may contain private hostnames; review the redacted preview before sending.
6. Build and start the service with `docker compose --env-file .env up -d --build`. Map the host port to a private interface or place the app behind a trusted HTTPS reverse proxy. Avoid making this management interface depend on the SWAG instance it controls for emergency access.
7. Before enabling activation against production, test with a disposable SWAG container using the same image and representative proxy files. Confirm valid, invalid, and conflicting edits, and inspect the retained backup and SWAG logs.

You can deploy with Docker Compose from the Unraid terminal (below), or with one Unraid Docker template (see [Using an Unraid Docker template](#using-an-unraid-docker-template)). Moving from the older two-container setup? See [Migrating from two containers](#migrating-from-two-containers) first.

### Step by step on Unraid

Run every command in this guide on the Unraid server, in its web terminal (the `>_` icon) or over SSH, not on another machine with the share mounted.

1. If you already created a `swag-ui` container from a template, remove it so it does not hold the port: `docker rm -f swag-ui`. Delete the template from the Docker tab as well.
2. Check that Compose is available with `docker compose version`. If it is not, install the **Compose Manager** plugin from Community Applications.
3. Download the code into an appdata folder. Unraid may not include `git`, so fetch the archive:

   ```sh
   mkdir -p /mnt/user/appdata/swag-ui && cd /mnt/user/appdata/swag-ui
   wget -qO- https://github.com/adhatcher/swag-ui/archive/refs/heads/main.tar.gz | tar xz --strip-components=1
   ```

   Any appdata location works. The Compose project takes its name from the folder, so the volume in this guide is named `swag-ui_app_data`.
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
   DOCKER_GID=$(stat -c %g /var/run/docker.sock)
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
   - `DOCKER_GID` lets the container use the Docker socket without running as root.
   - `SWAG_UI_BIND_ADDRESS` is the host IP the UI listens on. Use the server's LAN IP, or its Tailscale IP to keep the UI on your tailnet. Never forward this port from the internet.
   - `SWAG_UI_ALLOWED_HOSTS` lists every hostname or IP you will type in the browser, without the port.
   - `SWAG_UI_COOKIE_SECURE=false` is only for plain HTTP on a private network. Set it to `true` when the UI is served over HTTPS.
   - Optionally add `TAILSCALE_CONTAINER_NAME` and the `LITELLM_*` values from `.env.example`.
7. Build and start the container:

   ```sh
   cd /mnt/user/appdata/swag-ui && docker compose --env-file .env up -d --build --remove-orphans && docker compose ps
   ```

   `swag-ui` should report `healthy` within about a minute.
8. Open `http://<bind-address>:8022` and sign in. The overview should show SWAG as **running** and auto reload as **Disabled**. Make one harmless change, such as adding a comment line to a proxy file, and activate it. A green notice should say "Nginx accepted the reload signal. Verify the service before relying on the new route.", and `docker logs <swag container>` should show no errors.

To update later, repeat the download in step 3; it keeps your `.env`. Then run the command in step 7 again. To stop the app, run `docker compose down` in the same folder. That keeps the data volume; `docker compose down -v` deletes your drafts and backups.

### Using an Unraid Docker template

Use this instead of Compose if you prefer to manage the container from the Docker tab. It uses the published image, so Unraid's update check covers it. Replace the example IP, hostname and paths with your own; find SWAG's container name and `/config` folder with step 4 above.

1. In SWAG's template, add the variable `SWAG_AUTORELOAD` with the value `false` and apply.
2. Create the data folder, owned by the container user, and note the Docker socket's group id:

   ```sh
   mkdir -p /mnt/user/appdata/swag-ui/data && chown 99:100 /mnt/user/appdata/swag-ui/data
   stat -c %g /var/run/docker.sock
   ```

3. Generate the admin password and session secret, and keep the output for step 4. The first line is the sign-in password:

   ```sh
   openssl rand -base64 18; openssl rand -hex 32
   ```

4. In the Docker tab, click **Add Container**:

   | Field | Value |
   | --- | --- |
   | Name | `swag-ui` |
   | Repository | `ghcr.io/adhatcher/swag-ui:latest` |
   | Network Type | `Bridge` |
   | Port | Host `8022`, container `8080`. To listen on one IP only, as Compose does, leave this out and add `-p 192.168.1.10:8022:8080` to Extra Parameters |
   | Variable `SWAG_UI_ADMIN_PASSWORD` | First line from step 3 (at least 16 characters) |
   | Variable `SWAG_UI_SESSION_SECRET` | Second line from step 3 (at least 32 characters) |
   | Variable `SWAG_UI_ALLOWED_HOSTS` | Every hostname or IP you type in the browser, for example `192.168.1.10,tower.local` |
   | Variable `SWAG_UI_COOKIE_SECURE` | `false` for plain HTTP on a private network, `true` behind HTTPS |
   | Variable `SWAG_CONTAINER_NAME` | SWAG's container name, for example `swag` |
   | Variable `TAILSCALE_CONTAINER_NAME` | Optional. A Tailscale container with the CLI |
   | Variable `LITELLM_BASE_URL`, `LITELLM_MODEL`, `LITELLM_API_KEY` | Optional, for AI advice |
   | Path `/swag/proxy-confs` | `/mnt/user/appdata/swag/nginx/proxy-confs`, read/write |
   | Path `/swag/letsencrypt` | `/mnt/user/appdata/swag/etc/letsencrypt`, read only |
   | Path `/var/run/docker.sock` | `/var/run/docker.sock` |
   | Path `/data` | `/mnt/user/appdata/swag-ui/data` |
   | Extra Parameters | `--user 99:100 --group-add <gid from step 2> --read-only --tmpfs /tmp --security-opt no-new-privileges:true --cap-drop ALL` |

   Do not map SWAG's whole `/config`; it holds DNS credentials and private keys the app does not need.
5. Start `swag-ui`. Open `http://<server-ip>:8022` and sign in with the password. There is no username. The overview should show SWAG as **running** and auto reload as **Disabled**; then test one harmless activation as in step 8 above.

Notes:

- A Port field mapping listens on every host interface. Use the `-p <ip>:8022:8080` form in Extra Parameters to limit it to the LAN or Tailscale IP, and never forward the UI port from the internet.
- Drafts save even when activation is blocked. Activation needs `SWAG_AUTORELOAD=false` on SWAG's container (step 1).

### Migrating from two containers

Earlier versions ran a separate `swag-ui-helper` container that shared a socket with `swag-ui`. To move to the single container:

1. Stop and remove the helper: `docker rm -f swag-ui-helper`, and delete its template from the Docker tab. With Compose, run `docker compose down` in the app folder **before** downloading the new code, so the old `web` and `helper` containers are removed.
2. Copy any backups you want to keep into the new backup folder. With templates: `mkdir -p /mnt/user/appdata/swag-ui/data/backups && cp -a /mnt/user/appdata/swag-ui/backups/. /mnt/user/appdata/swag-ui/data/backups/`. With Compose: `docker run --rm -v swag-ui_backups:/old:ro -v swag-ui_app_data:/data alpine sh -c 'mkdir -p /data/backups && cp -a /old/. /data/backups/'`.
3. Give the data folder to the new container user. The old image wrote it as uid 10001, and the new one runs as 99:100 and refuses to start if it cannot write there. With templates: `chown -R 99:100 /mnt/user/appdata/swag-ui/data`. With Compose: `docker run --rm -v swag-ui_app_data:/data alpine chown -R 99:100 /data`.
4. With templates, edit the `swag-ui` template: remove the `/run/swag-ui` path, change `/swag/proxy-confs` to read/write, add the `/swag/letsencrypt`, `/var/run/docker.sock`, `SWAG_CONTAINER_NAME` and `TAILSCALE_CONTAINER_NAME` entries, and set Extra Parameters as in step 4 of [Using an Unraid Docker template](#using-an-unraid-docker-template). With Compose, download the new code, add `DOCKER_GID` to `.env` (`echo "DOCKER_GID=$(stat -c %g /var/run/docker.sock)" >> .env`), and run step 7 of the Compose guide.
5. Apply, then confirm the overview shows SWAG **running** and auto reload **Disabled**. Once you have confirmed the backups were copied, you can delete the old `backups` folder or the `swag-ui_backups` and `swag-ui_control_socket` volumes.

### Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `docker: invalid reference format` when applying a template | The template's Repository field holds a command such as `docker pull ghcr.io/...`. It must hold only an image name, such as `ghcr.io/adhatcher/swag-ui:latest`. |
| `ValueError: SWAG_UI_ADMIN_PASSWORD must be at least 16 characters` (or `SWAG_UI_SESSION_SECRET ... 32`) in the logs | The container started without those settings, or with values that are too short. With Compose, set both in `.env` and start with `docker compose --env-file .env`. With templates, add both variables to the `swag-ui` template as in step 4 of the template steps. |
| `ValueError: /data is not writable by uid 99` in the logs | The data folder or volume belongs to another user, often uid 10001 from the old image. Run `chown -R 99:100` on it as in step 3 of [Migrating from two containers](#migrating-from-two-containers). |
| `required variable DOCKER_GID is missing a value` from Compose | Add `DOCKER_GID=$(stat -c %g /var/run/docker.sock)` to `.env`. |
| Overview shows SWAG `unavailable` | The container cannot use the Docker socket, or `SWAG_CONTAINER_NAME` is wrong. Check that `/var/run/docker.sock` is mapped, that the `--group-add` value (or `DOCKER_GID`) matches `stat -c %g /var/run/docker.sock`, and that the name matches `docker ps`. |
| Activation says `blocked: Proxy folder is not writable by this container` | The proxy folder is mapped read-only, or is not writable by 99:100. Map `/swag/proxy-confs` read/write and check the folder's owner with `ls -ln`. |
| Activation says `blocked: <file> is owned by uid N` | That proxy file was created by another user, often root from a shell. Run `chown 99:100` on the file, then apply again. |
| Auto reload shows **Unknown**, or activation says `blocked: SWAG_AUTORELOAD must be explicitly false` | SWAG's container has no `SWAG_AUTORELOAD` variable, or its value is not `false`. Your draft is still saved; only activation is blocked. Add the variable with the value `false` in SWAG's template and apply, then confirm with `docker inspect <swag container> --format '{{range .Config.Env}}{{println .}}{{end}}' \| grep AUTORELOAD`. |
| `Invalid host header` in the browser | The hostname or IP in the address bar is missing from `SWAG_UI_ALLOWED_HOSTS`. Add it and run `docker compose --env-file .env up -d`. |
| Sign-in fails with `Invalid CSRF token` over plain HTTP | `SWAG_UI_COOKIE_SECURE=true` makes the browser discard the session cookie on an `http://` address. Use HTTPS, or set it to `false` on a private network. |
| `Bind for ... failed: port is already allocated` | Another container already uses the port, often a leftover template container. Remove it or change `SWAG_UI_PORT`. |
| No proxy files listed | `SWAG_CONFIG_PATH` (or the template's `/swag/proxy-confs` path) does not point at the folder containing the proxy files, or they are not readable by 99:100. |

Ports and volumes:

| Service | Exposure | Persistent or host mounts |
| --- | --- | --- |
| `swag-ui` | Host `${SWAG_UI_BIND_ADDRESS}:${SWAG_UI_PORT}` to container `8080` | `app_data` at `/data` for drafts, audit and backups; proxy directory read-write; Let's Encrypt metadata read-only; Docker socket |

`/data/backups` keeps a copy of the original file for each activation. Docker logs show service health and errors, without logging credentials or draft contents. The image has an HTTP health check on `/healthz`.

## Activation and recovery

Activation checks the current file hash, checks that the container can replace the file, backs up the original to `/data/backups` outside SWAG's watched tree, atomically replaces the file, runs `nginx -t`, then asks Nginx to reload. A failed syntax test restores the file without a reload. A failed reload restores the file and reports **active state uncertain** because the running configuration cannot be inferred from a failed signal alone. A successful reload command is reported as **reload requested**, not as proof that every route works. Apply runs to completion within the request, which can take a while if SWAG is slow to answer; if you put a reverse proxy in front of the UI, give it a read timeout above 150 seconds.

If active state is uncertain, stop applying drafts. Inspect the SWAG container logs and running routes, compare the current file with the backup in `/data/backups`, restore the original if needed, run `nginx -t` in SWAG, and manually reload or restart only after the original configuration is known good. External edits or manual reloads are outside the app's lock; the hash check detects a changed file before activation but cannot prevent another administrator from acting simultaneously.

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

GitHub Actions uses the same pinned shared workflow family as Bourbon Book: CI checks and a build of the container image on pull requests, CodeQL for Actions/TypeScript/Python, and GHCR publication of the full app image only after successful CI on `main`. GitHub execution is verified when the first PR runs; local checks do not establish the remote result.

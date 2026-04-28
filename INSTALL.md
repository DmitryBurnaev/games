# Installation Guide

## Server Requirements

- Ubuntu/Debian server
- Docker and Docker Compose plugin
- Nginx for public reverse proxy
- 512 MB RAM minimum
- 1 CPU core minimum

## Service Installation

The server stores compose/config/scripts plus `.env`, `.version`, and `.data`;
application code runs from the immutable Docker image pushed by GitHub Actions.

```shell
ssh ${TARGET_SERVER}
sudo su

export TARGET_DIR="/opt/games"

groupadd --system games-srv --gid 1010
useradd --no-log-init --system --gid games-srv --uid 1010 games-srv

mkdir -p ${TARGET_DIR}/bin ${TARGET_DIR}/.data
chown games-srv:games-srv -R ${TARGET_DIR}
usermod -a -G docker games-srv
chmod -R 660 ${TARGET_DIR}
chmod -R ug+x ${TARGET_DIR}/bin
chmod ug+x ${TARGET_DIR} ${TARGET_DIR}/.data
chmod ug+w ${TARGET_DIR}/.data
```

CI deploys over SSH as a non-root user, for example `deploy`.

```shell
usermod -a -G games-srv deploy
visudo -f /etc/sudoers.d/deploy
```

Add:

```text
deploy ALL = NOPASSWD: /bin/systemctl restart games.service
deploy ALL = NOPASSWD: /bin/systemctl show -p ActiveState --value games
```

After the first CI delivery, prepare environment values:

```shell
cp ${TARGET_DIR}/.env.template ${TARGET_DIR}/.env
nano ${TARGET_DIR}/.env
chown games-srv:root ${TARGET_DIR}/.env
chmod 400 ${TARGET_DIR}/.env
```

Required production values:

- `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS` when HTTPS/domain CSRF protection needs it
- `APP_PORT`
- `SQLITE_PATH`, normally `/app/.data/db.sqlite3`

The SQLite database is stored on the host at `${TARGET_DIR}/.data/db.sqlite3`
through the Docker bind mount `${TARGET_DIR}/.data:/app/.data`.
The app container runs as UID/GID `1010`, so `${TARGET_DIR}/.data` must be writable
by that ID. The commands above create the directory under the matching service user.

Create the systemd service:

```shell
ln -s ${TARGET_DIR}/games.service /etc/systemd/system/games.service
systemctl daemon-reload
systemctl enable games.service
systemctl start games.service

systemctl status games.service
journalctl -u games
```

## Deployment User

CI deploys over SSH as a non-root user, for example `deploy`.

```shell
usermod -a -G games-srv deploy
visudo -f /etc/sudoers.d/deploy
```

Add:

```text
deploy ALL = NOPASSWD: /bin/systemctl restart games.service
deploy ALL = NOPASSWD: /bin/systemctl show -p ActiveState --value games
```

GitHub Actions secrets:

- `SSH_PKEY`
- `SSH_PORT`
- `SSH_USER`
- `SSH_HOST`
- `PROD_PROJECT_ROOT`, usually `/opt/games`

## Release Flow

1. Push to `main` runs Dockerized lint/tests.
2. Push a semver tag such as `0.1.0`.
3. GitHub Actions builds the `service` Docker target, including collected static files.
4. The image is pushed to GHCR as `<version>` and `latest`.
5. CI copies `etc/docker-compose.yml`, `etc/bin/*`, `.env.template`, `games.service`,
   and `nginx.conf` to the server.
6. CI writes `.version` with `DOCKER_IMAGE=<versioned-ghcr-image>`.
7. `bin/deploy` pulls the image, restarts systemd, prunes old images, and prints status.

## Service Management

On the server:

```shell
cd /opt/games

bin/service start
bin/service stop
bin/service restart
bin/service status
bin/service health
bin/service logs --tail 100
bin/service logs --follow
bin/service logs --grep error
```

Create an admin user after the service is running:

```shell
bin/service create-admin
```

The command opens Django's interactive superuser prompt inside the running app
container. `docker compose exec` allocates an interactive TTY by default, so the
script does not need an explicit `-it`. Use this account to sign in at `/gadm/`.
If you enabled the optional Nginx allowlist for `/gadm/`, run the command from
any SSH session as usual; the IP restriction only applies to browser access
through Nginx.

## Nginx

Copy `nginx.conf` to your Nginx site config and edit the domain and port:

```shell
export TARGET_DIR="/opt/games"

cp ${TARGET_DIR}/nginx.conf /etc/nginx/sites-available/games.conf
ln -s /etc/nginx/sites-available/games.conf /etc/nginx/sites-enabled/
nano /etc/nginx/sites-available/games.conf
nginx -t && nginx -s reload
```

Set up HTTPS with Certbot after DNS points to the server:

```shell
apt update
apt install certbot python3-certbot-nginx
certbot --nginx -d games.example.com
systemctl status certbot.timer
```

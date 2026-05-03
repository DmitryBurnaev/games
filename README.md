# Games

Web platform for small online games. The first implemented game is two-player
long backgammon.

The app is a Django project with server-rendered pages, a browser-side board UI,
Docker packaging, SQLite persistence, and GitHub Actions for test/release/deploy.

## Implemented Games

- Long backgammon: online two-player game with lobby, joining, rolling dice,
  legal moves, undo during unfinished turns, bearing off, win detection, and
  player stats foundation.

## Requirements

- Python 3.14
- `uv`
- Docker and Docker Compose for containerized runs

## Quick Start

```bash
make install
make migrate
make run
```

The local non-Docker app uses SQLite at `.data/db.sqlite3` by default.

Open:

```text
http://127.0.0.1:8000/
```

## Docker Run

Create `.env` from the template:

```bash
make env
```

For production-like local Docker, set `DJANGO_SECRET_KEY` in `.env`. Then run:

```bash
make run-in-docker
```

Docker stores the SQLite database on the host in:

```text
.data/db.sqlite3
```

The host directory `.data` is mounted into the container as `/app/.data`.
On Linux hosts this directory must be writable by UID/GID `1010`, the non-root user
used by the container.

## Make Commands

```bash
make help
```

Common commands:

```bash
make install         # install dependencies with uv
make env             # create .env from .env.template if it does not exist
make check           # run Django system checks
make test            # run Django tests for backgammon
make lint            # run black --check and Django checks
make format          # format Python code with black
make migrate         # apply Django migrations locally
make run             # start Django dev server
make run-in-docker   # build and run app with Docker Compose
make test-in-docker  # run tests in Docker
make lint-in-docker  # run lint checks in Docker
```

## Environment Variables

Runtime variables are defined in `.env.template`.

| Variable                      | Required               | Default                                 | Description                                                               |
|-------------------------------|------------------------|-----------------------------------------|---------------------------------------------------------------------------|
| `DOCKER_IMAGE`                | Docker/prod            | `games:latest`                          | Image used by Compose. In production this is written to `.version` by CI. |
| `APP_HOST`                    | No                     | `localhost`                             | Informational host value for local/server scripts.                        |
| `APP_PORT`                    | Yes for Docker/prod    | `8000`                                  | Port exposed by uvicorn and bound on `127.0.0.1`.                         |
| `DJANGO_DEBUG`                | No                     | `false` in template, `true` without env | Enables Django debug mode. Keep `false` in production.                    |
| `DJANGO_SECRET_KEY`           | Yes for Docker/prod    | none                                    | Django secret key. Generate a strong unique value for production.         |
| `DJANGO_ALLOWED_HOSTS`        | Yes for Docker/prod    | `localhost,127.0.0.1`                   | Comma-separated Django allowed hosts.                                     |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | For HTTPS/domain forms | empty                                   | Comma-separated trusted origins, for example `https://games.example.com`. |
| `DJANGO_USE_X_FORWARDED_HOST` | No                     | `false`                                 | Whether Django should trust `X-Forwarded-Host`.                           |
| `BACKGAMMON_DEBUG_TOOLS`      | No                     | follows `DJANGO_DEBUG`                  | Enables development helper buttons in the game UI.                        |
| `BACKGAMMON_ANIMATIONS_ENABLED` | No                   | `true`                                  | Enables checker movement animations in the board UI.                      |
| `BACKGAMMON_POLL_INTERVAL_MS` | No                     | `1000`                                  | Browser polling interval for refreshing active game state, in milliseconds. |
| `ALLOW_USER_REGISTRATION`     | No                     | `false`                                 | Enables the public signup page and account creation when set to `true`.   |
| `SQLITE_PATH`                 | Yes for Docker/prod    | `/app/.data/db.sqlite3` in template     | SQLite database path inside the container.                                |

Local non-Docker runs can omit `.env`; settings default to `.data/db.sqlite3`.

Example production-ish `.env`:

```dotenv
DOCKER_IMAGE="games:latest"
APP_HOST=localhost
APP_PORT=8000

DJANGO_DEBUG=false
DJANGO_SECRET_KEY=replace-with-a-strong-secret
DJANGO_ALLOWED_HOSTS=games.example.com,localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://games.example.com
DJANGO_USE_X_FORWARDED_HOST=false
BACKGAMMON_DEBUG_TOOLS=false
BACKGAMMON_ANIMATIONS_ENABLED=true
BACKGAMMON_POLL_INTERVAL_MS=1000
ALLOW_USER_REGISTRATION=false

SQLITE_PATH=/app/.data/db.sqlite3
```

## GitHub Actions

Workflows:

- `tests.yaml`: runs on PRs to `main` and pushes to `main`; builds Docker test image,
  runs Dockerized lint, then Dockerized tests.
- `release.yaml`: runs on semver tags like `0.1.0`; builds and pushes the service image
  to GHCR, creates a GitHub Release, copies deployment files to the server, and runs deploy.
- `scan.yaml`: runs secret/static scans on `main` and weekly.
- `codeql.yaml`: runs CodeQL Python analysis on `main` and weekly.

### CI/CD Secrets

Release deployment needs these repository secrets:

| Secret              | Description                                       |
|---------------------|---------------------------------------------------|
| `SSH_PKEY`          | Private SSH key used by the deploy job.           |
| `SSH_PORT`          | SSH port on the target server.                    |
| `SSH_USER`          | SSH deploy user.                                  |
| `SSH_HOST`          | Target server host/IP.                            |
| `PROD_PROJECT_ROOT` | Project root on the server, usually `/opt/games`. |

GitHub's built-in `GITHUB_TOKEN` is used to push images to GHCR and create releases.

## Release

Create and push a semver tag:

```bash
git tag 0.1.0
git push origin 0.1.0
```

The release workflow publishes:

- `ghcr.io/<owner>/<repo>:0.1.0`
- `ghcr.io/<owner>/<repo>:latest`

Then it writes `.version` on the server and runs `bin/deploy`.

## Server

See `INSTALL.md` for full setup. In short, production uses:

- Docker Compose with only the app container.
- SQLite persisted in `/opt/games/.data/db.sqlite3`.
- `/opt/games/.data` must be writable by UID/GID `1010`.
- systemd service `games.service`.
- Nginx as reverse proxy to `127.0.0.1:${APP_PORT}`.
- `/health/` as an unauthenticated health endpoint.

Server helper examples:

```bash
cd /opt/games
bin/service status
bin/service logs --tail 100
bin/service restart
bin/service health
```

## Useful Direct Commands

```bash
uv run python src/manage.py check
uv run python src/manage.py test backgammon
uv run black --check .
docker compose config
docker compose up --build app
docker compose up --build --exit-code-from test test
docker compose up --build --exit-code-from lint lint
```

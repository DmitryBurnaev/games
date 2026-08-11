# Project Notes For Future Codex Sessions

## Project Shape

- Django 6.0 app for an online two-player long backgammon game (`длинные нарды`).
- Main app: `src/backgammon`.
- Django templates render the UI; Bootstrap is used where convenient.
- Game logic lives mostly in `src/backgammon/services.py`.
- Browser UI logic lives in `src/backgammon/static/backgammon/game.js`.
- Board styling lives in `src/backgammon/static/backgammon/game.css`.
- Tests live in `src/backgammon/tests.py`.
- Game rules source: `.local/game-rules.md`.

Useful checks:

```bash
make check
make test
make lint
make test-in-docker
uv run python src/manage.py check
uv run python src/manage.py test backgammon
uv run black --check .
docker compose up --build app
docker compose up --build --exit-code-from test test
```

## Change Map

Start from the smallest relevant ownership boundary:

- Rule validation, legal moves, dice behavior, undo, victory handling, test board
  arrangements, and browser state serialization: `src/backgammon/services.py`.
- HTTP actions, lobby decoration, transactions, row locking, and realtime
  publication after mutations: `src/backgammon/views.py`.
- Persistent game shape and runtime-editable settings: `src/backgammon/models.py`.
- Runtime setting resolution with database override and environment fallback:
  `src/backgammon/app_settings.py`.
- WebSocket fan-out: `src/backgammon/realtime.py`,
  `src/backgammon/consumers.py`, and `src/backgammon/routing.py`.
- Browser state rendering, interactions, animations, polling fallback, and
  WebSocket reconnect logic: `src/backgammon/static/backgammon/game.js`.
- Board and side-panel layout: `src/backgammon/static/backgammon/game.css`.
- Server-rendered shell and lobby: `src/backgammon/templates/backgammon`.
- Environment-backed Django configuration: `src/games/settings.py`.
- ASGI HTTP/WebSocket routing: `src/games/asgi.py`.

Prefer extending these existing paths over introducing parallel implementations.

## Deployment In This Project

The project has Docker, server deployment files, and GitHub Actions release automation.

Local and CI files:

- `Dockerfile`: multi-stage build with `service` and `tests` targets.
- `docker-compose.yml`: local/CI compose with `app`, `test`, and `lint`.
- `.env.template`: local/server environment template; real `.env` is ignored.
- `.dockerignore`: keeps local caches, data, static output, and git metadata out of images.
- `Makefile`: common local commands for check/test/lint/Docker runs.

Server/deployment files:

- `etc/docker-compose.yml`: production compose that pulls `${DOCKER_IMAGE}` instead of building.
- `etc/docker-entrypoint`: validates env, runs migrations, and starts app/test/lint modes.
- `etc/games.service`: systemd unit for `/opt/games`.
- `etc/nginx.conf`: reverse proxy template to the app bound on localhost.
- `etc/bin/start`, `etc/bin/stop`, `etc/bin/deploy`, `etc/bin/service`: server operation scripts.
- `INSTALL.md`: server bootstrap, deployment user, release flow, and Nginx/Certbot instructions.

GitHub Actions:

- `.github/workflows/tests.yaml`: Dockerized lint and tests on PR/main.
- `.github/workflows/release.yaml`: semver tag release, GHCR image push, GitHub Release, SSH deploy.
- `.github/workflows/scan.yaml`: TruffleHog, Gitleaks, and Opengrep scans.
- `.github/workflows/codeql.yaml`: CodeQL Python analysis.

Runtime notes:

- Django settings are env-driven in `src/games/settings.py`.
- Production requires `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS`.
- The app uses SQLite. Docker mode mounts host `./.data` to container `/app/.data`;
  the database file is `/app/.data/db.sqlite3` unless `SQLITE_PATH` says otherwise.
- Local non-Docker mode defaults to `.data/db.sqlite3`.
- Static files are collected into the immutable Docker image and served by WhiteNoise.
- `/health/` is unauthenticated and used by service health checks.
- The app runs with `uvicorn games.asgi:application` in the production container.
- Django Channels provides WebSocket updates. Docker and production use Redis at
  `REDIS_URL`; local non-Docker development falls back to an in-memory channel layer.
- `ALLOW_USER_REGISTRATION` controls whether the public signup page accepts and
  advertises new account creation.
- `APP_VERSION` is displayed in the shared navbar. Release deployment writes it
  from the semver tag through the server `.version` file.

## Game Flow

The intended turn flow is explicit:

1. Current player rolls dice.
2. Player makes all legal moves for the rolled dice.
3. During this unfinished turn, player may undo their own latest checker move.
4. When no legal moves remain, player clicks `Завершить ход`.
5. Only then the turn switches to the opponent.

Important: `apply_move()` must not auto-switch the turn after the last move. Turn switching happens through `finish_blocked_turn()`.

Button state expectations:

- `Бросить кубики` is active only for the current player before rolling.
- `Отменить мой ход` is active only during the current player's unfinished rolled turn, and only if the latest action is that player's checker move.
- `Завершить ход` is active only for the current player after rolling when there are no legal moves left.
- If the turn is on the opponent side, no control button should be active for the viewer.
- Debug helper buttons and endpoints are controlled by `backgammon_debug_tools()`.
  Its environment fallback is `BACKGAMMON_DEBUG_TOOLS`, which defaults to Django
  `DEBUG`; an enabled `AppSetting` row can override it at runtime.
- `В дом для теста` is a development helper that moves the viewer's checkers into their home area, resets dice, and makes it their turn so bearing-off/final-game mechanics can be tested quickly.
- `Тест победы` is a development helper that marks 13 viewer checkers borne off, leaves 2 in home, resets dice, and makes it their turn for fast victory-animation testing.
- `Тест головы 5/5` is a development helper for a first-turn position where only an
  additional head move is possible.
- `Тест блока 6` is a development helper for a six-point block that must be broken
  before the current turn can finish.

## State Mutation And Realtime

Player actions use HTTP POST endpoints; WebSockets distribute fresh state after
successful mutations.

Normal participant mutation contract:

1. The view enters `transaction.atomic()`.
2. The game is fetched with `select_for_update()` through `get_participant_game()`.
3. A service function validates and mutates the game.
4. The view serializes the updated viewer-specific payload.
5. `queue_game_update()` schedules publication with `transaction.on_commit()`.

Do not publish realtime events before the database commit. Do not duplicate rule
validation in JavaScript; the frontend consumes backend-calculated `legal_moves`
and capability flags such as `can_roll`, `can_end_turn`, and `can_undo`.

`join_game()` is the deliberate exception to the helper path: it locks the waiting
game row directly with `select_for_update()` before assigning the second player.

Realtime flow:

- HTTP state fallback: `/games/<game_id>/state/`.
- WebSocket endpoint: `/ws/games/<game_id>/`.
- `GameStateConsumer` sends an initial viewer-specific snapshot after connect.
- Backend updates publish a lightweight group event; every consumer serializes a
  fresh payload for its own authenticated viewer.
- Waiting games may be viewed before joining. Active and finished games are
  restricted to their participants.
- The browser sends heartbeat pings, reconnects with bounded backoff, and enables
  polling fallback when WebSocket delivery is unavailable or stale.

Production WebSockets depend on the `/ws/` Nginx upgrade proxy and Redis-backed
Channels layer. Preserve the original `Host` and `X-Forwarded-Proto` headers.

## Time And Player Labels

- `Game.started_at` records the real start of play on the first dice roll, not when
  a waiting game is created or when the second player joins.
- Older rows and lobby decoration fall back from `started_at` to `created_at`.
- Active-game elapsed time is rendered client-side from `started_at` and updates
  without waiting for a server event.
- The active-game timer belongs in the bottom-left corner of the side panel,
  opposite the surrender button, and is displayed as an hourglass plus duration.
- Finished games use `finished_at` as the stable duration endpoint.
- User-facing player labels prefer `user.get_full_name()` and fall back to
  `username`. JSON payloads expose this as `display_name`.

## Undo Behavior

Undo is intentionally scoped to the current unfinished turn:

- A player can undo only their latest checker move (`move` or `bear_off`).
- Undo restores the checker, remaining dice move, and `head_moves_this_turn`.
- Undo is blocked after `Завершить ход`.
- Undo is blocked for finished games.
- Undo deletes the undone `GameMove` record instead of adding a separate undo action.

Key functions:

- `can_undo_last_move()`
- `undo_last_move()`
- `restore_head_moves_count()`

## Last-Move Highlighting

The UI highlights moved checkers to help players orient on the board.

Desired behavior:

- During your unfinished turn, highlight all checkers moved since your roll.
- After you click `Завершить ход`, your own highlight disappears for you.
- The opponent still sees your moved-checker highlight until they roll dice.
- Once the opponent rolls, the previous highlight clears.
- If multiple moved checkers land on the same point, highlight that many top visible checkers in the stack.

Implementation:

- Backend exposes `last_move_markers` in serialized game state.
- `last_move_markers` groups moved checker moves by target point and includes `count`.
- Frontend uses `movedCheckerCount()` and adds `last-move-checker` to the top N visible checkers in that point.
- `last_move_marker` is kept as a legacy/single-marker field, but new UI should prefer `last_move_markers`.

Key functions/classes:

- `checker_moves_for_current_roll()`
- `move_marker_player()`
- `last_move_markers()`
- `.checker.last-move-checker`
- `.point.last-move-point`

## Board Orientation And UI

Each player sees their home zone at the bottom-left.

Board rendering:

- White view:
  - top row: points `23..12`
  - bottom row: points `0..11`
- Black view:
  - top row: points `11..0`
  - bottom row: points `12..23`

Destination selection:

- Player clicks a legal source point, then clicks the highlighted target point directly on the board.
- Points with own checkers may be both a source and a target. Target highlighting should take priority when a source is already selected.

Visual style:

- Keep interface wording concise; prefer the shortest clear label.
- Wooden board with a vertical center divider.
- Point numbering is hidden during ordinary play and shown only with debug tools.
- Checkers are volumetric, checker-like discs.
- Checker diameter is tied to point width (`90%`).
- Dice rolling animation uses rotation and blurred changing values.
- Dice values that still have unspent moves are highlighted.
- When a double is rolled, the UI randomly shows `domovoy-1.jpg` or `domovoy-2.jpg` at bottom-left for a short moment.
- When the game finishes, the board shows a victory banner and the same random domovoy pop-up.
- Side panel is green on viewer turn and gray/pulsing while waiting.

## Data Model Notes

Core models:

- `Game`: game state, board JSON, dice JSON, current player, winner, status, victory type.
- `GameMove`: history of joins, rolls, moves, bear-offs, finishes.
- `PlayerStats`: foundation for future statistics.

`Game.board` is a list of 24 items. Each item is either `None` or:

```python
{'color': 'white' | 'black', 'count': int}
```

Initial board:

- White has 15 checkers at point `0`.
- Black has 15 checkers at point `12`.

## Long Backgammon Rule Notes

Movement paths:

- White: `0..23`
- Black: `12..23, 0..11`

Heads:

- White head: `0`
- Black head: `12`

Important rule details implemented:

- Cannot land on a point occupied by opponent checkers.
- Usually only one checker can be moved from the head per turn.
- On the first turn, doubles `3`, `4`, or `6` may allow taking two checkers from the head.
- Six-checker blocks are restricted when no opponent checker is ahead.
- Bearing off is allowed only when all player checkers are in home.
- Win type is `mars` if opponent has borne off zero checkers, otherwise `oin`.

## Skipped-Dice Statistics

Finished-game statistics derive skipped dice from `GameMove` history; no game-state
field is stored for them.

- A "skipped move" is one unspent die, not a whole player turn.
- A regular roll provides two dice moves. Every unspent die adds one skipped move
  and its face value to skipped points.
- A double provides four dice moves. For example, after a `6/6` roll where only
  one six is used, the player has three skipped moves and 18 skipped points.
- A turn spans from its `ROLL` event to the next `ROLL` event. `MOVE` and
  `BEAR_OFF` distances consume matching dice; the remaining dice form the skipped
  statistics.
- The winning roll and every die from it are excluded from skipped statistics,
  total-points statistics, and double usage statistics, even when the player did
  not consume every die before winning.

## Runtime Game Settings

Backgammon behavior can be changed through environment-backed Django settings and,
for selected keys, through enabled `AppSetting` rows in Django admin.

Database override behavior:

- An enabled valid `AppSetting` row overrides its environment-backed setting.
- Missing, disabled, or invalid rows fall back to `src/games/settings.py`.
- The migration seeds known rows as disabled so deployments keep existing
  environment behavior until an admin opts in.

Supported runtime keys:

- `BACKGAMMON_DEBUG_TOOLS`: show or hide development helpers and their endpoints.
- `BACKGAMMON_DICE_MODE`: `independent` or `player_bag`.
- `BACKGAMMON_ANIMATIONS_ENABLED`: enable or disable checker movement animations.
- `BACKGAMMON_POLL_INTERVAL_MS`: polling fallback interval, clamped to at least
  `250` milliseconds.
- `BACKGAMMON_CHECKER_COUNT_PRESETS`: comma-separated checker-count choices for
  the game setup dialog, from `1` to `20`; `15` is always available.

Dice modes:

- `independent`: roll each die independently with OS randomness.
- `player_bag`: each player gets a per-game cycle of all 36 ordered dice pairs;
  each pair appears once per personal cycle before the bag resets.

## Development Cautions

- Do not rewrite unrelated UI/style work casually; the board has been tuned iteratively.
- Keep game-rule changes covered by tests in `src/backgammon/tests.py`.
- Cache-bust static assets in templates when changing `game.js` or `game.css`.
- Avoid migrations unless model fields actually change; recent marker/undo behavior is computed from `GameMove` history.
- Preserve the explicit HTTP-mutation/WebSocket-notification split.
- Use `GameMove` history when extending turn-local UI behavior such as highlights,
  animation replay, or undo. Avoid adding stored model state when history already
  provides the answer.
- Keep viewer-specific data in `serialize_game()`; controls and legal moves differ
  between the two players.

## Verification Expectations

For ordinary backend or UI changes, run:

```bash
make format
make lint
make test
```

Also run `node --check src/backgammon/static/backgammon/game.js` after changing
browser JavaScript.

For layout or interaction changes, verify the affected local page in a browser.
Check both a regular desktop layout and a narrow layout when side-panel height or
board responsiveness may be affected.
Treat browser verification as required for every repository enhancement or bug fix
that affects user-visible behavior; do not consider automated checks alone
sufficient when the affected page can be run locally.

For Docker, deployment, Channels, Redis, or entrypoint changes, also run the
relevant Compose path:

```bash
make test-in-docker
make lint-in-docker
```

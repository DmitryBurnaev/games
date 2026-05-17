# WebSocket Game State Plan

## Goal

Move active game boards from constant HTTP polling to WebSocket-driven realtime
state delivery, while keeping HTTP polling as a reliable fallback.

## Architecture

- Player actions stay as existing HTTP POST requests (`roll`, `move`, `undo`,
  `end_turn`, `surrender`, and debug helpers).
- Every successful state-changing HTTP request publishes a lightweight
  `game.updated` event after the database transaction commits.
- Open board pages subscribe to a per-game WebSocket group.
- Each WebSocket consumer serializes the game state for its own viewer before
  sending it, because fields such as `viewer_color`, `legal_moves`, `can_roll`,
  and `last_move_markers` are viewer-specific.
- `GET /games/<id>/state/` remains the initial sync and fallback endpoint.

## Backend Work

1. Add Django Channels and a Redis channel layer.
2. Route `/ws/games/<game_id>/` through ASGI with Django session authentication.
3. Reject anonymous users and users who cannot view the requested game.
4. Accept participants and allowed waiting-game viewers, add them to
   `backgammon_game_<id>`, and send an initial `game_state` message.
5. Support application-level heartbeat messages:
   - browser sends `{ "type": "ping" }`;
   - server replies `{ "type": "pong" }`.
6. Publish `game.updated` from HTTP views with `transaction.on_commit(...)`.

## Frontend Work

1. Load the page with an initial HTTP state fetch.
2. Try to open a WebSocket connection to `/ws/games/<id>/`.
3. Apply incoming `game_state` messages through the existing `applyGameState`
   rendering path.
4. Send heartbeat pings periodically.
5. Track the timestamp of the latest `game_state` or `pong`.
6. Start legacy polling when:
   - WebSocket is unsupported;
   - connection fails or closes;
   - no heartbeat or state message arrives before the timeout.
7. Continue reconnect attempts with bounded backoff.
8. Stop polling once WebSocket is healthy again.

## Deployment Work

- Add Redis to local and production Docker Compose.
- Add `REDIS_URL` to `.env.template` and deployment docs.
- Update Nginx with a dedicated `/ws/` location that forwards WebSocket upgrade
  headers and keeps idle sockets alive long enough for heartbeats.

## Verification

- Django system check passes.
- Backgammon tests pass.
- Consumer tests cover allowed and rejected connections, initial state delivery,
  heartbeat, and update broadcasts.
- Existing HTTP state endpoint remains functional.

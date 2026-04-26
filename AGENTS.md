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
uv run python src/manage.py check
uv run python src/manage.py test backgammon
```

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
- Debug helper buttons are controlled by `settings.BACKGAMMON_DEBUG_TOOLS`, sourced from the `BACKGAMMON_DEBUG_TOOLS` environment variable. It defaults to Django `DEBUG`; use `0/false/off/no` to hide helpers even in development.
- `В дом для теста` is a development helper that moves the viewer's checkers into their home area, resets dice, and makes it their turn so bearing-off/final-game mechanics can be tested quickly.
- `Тест победы` is a development helper that marks 13 viewer checkers borne off, leaves 2 in home, resets dice, and makes it their turn for fast victory-animation testing.

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

- Wooden board with a vertical center divider.
- No visible point numbering.
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

## Development Cautions

- Do not rewrite unrelated UI/style work casually; the board has been tuned iteratively.
- Keep game-rule changes covered by tests in `src/backgammon/tests.py`.
- Cache-bust static assets in templates when changing `game.js` or `game.css`.
- Avoid migrations unless model fields actually change; recent marker/undo behavior is computed from `GameMove` history.

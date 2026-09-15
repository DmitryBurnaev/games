"""Backgammon's project-level adapter registration.

Rules, persistence, and HTTP/WebSocket behavior remain in the backgammon app.
Only cross-game navigation metadata is exposed to the project registry.
"""

from games.game_registry import GameType, game_registry

BACKGAMMON_GAME_TYPE = game_registry.register(
    GameType(
        key="backgammon",
        title="Длинные нарды",
        lobby_url_name="backgammon:game_list",
        detail_url_name="backgammon:game_detail",
    )
)

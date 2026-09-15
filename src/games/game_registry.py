"""Registry of independently routable game types.

The project registry intentionally contains metadata only.  A game application
owns its models, rules, state serialization, and transport; the project needs
only a stable key and routes to present or link that game in the future.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.urls import reverse


@dataclass(frozen=True)
class GameType:
    """Public metadata and route adapter for one independently owned game."""

    key: str
    title: str
    lobby_url_name: str
    detail_url_name: str

    def lobby_url(self) -> str:
        """Return the game's lobby route without knowing its implementation."""
        return reverse(self.lobby_url_name)

    def detail_url(self, game_id: int) -> str:
        """Return one game detail route through the game's own URL namespace."""
        return reverse(self.detail_url_name, kwargs={"pk": game_id})


class GameTypeRegistry:
    """Keep game-type registration explicit and reject accidental collisions."""

    def __init__(self) -> None:
        self._game_types: dict[str, GameType] = {}

    def register(self, game_type: GameType) -> GameType:
        """Register one game type, allowing Django's idempotent app startup."""
        existing = self._game_types.get(game_type.key)
        if existing and existing != game_type:
            raise ValueError(f"Game type '{game_type.key}' is already registered.")
        self._game_types[game_type.key] = game_type
        return game_type

    def get(self, key: str) -> GameType:
        """Return a registered game type or raise a clear configuration error."""
        try:
            return self._game_types[key]
        except KeyError as exc:
            raise LookupError(f"Unknown game type '{key}'.") from exc

    def all(self) -> tuple[GameType, ...]:
        """Return game types in stable key order for future project navigation."""
        return tuple(self._game_types[key] for key in sorted(self._game_types))


game_registry = GameTypeRegistry()

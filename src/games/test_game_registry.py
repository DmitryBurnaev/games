from django.test import SimpleTestCase

from backgammon.game_type import BACKGAMMON_GAME_TYPE
from games.game_registry import GameType, GameTypeRegistry, game_registry


class GameTypeRegistryTests(SimpleTestCase):
    """The generic registry stays independent from backgammon rules."""

    def test_backgammon_registers_routes_through_project_adapter(self) -> None:
        game_type = game_registry.get("backgammon")

        self.assertEqual(game_type, BACKGAMMON_GAME_TYPE)
        self.assertEqual(game_type.lobby_url(), "/")
        self.assertEqual(game_type.detail_url(42), "/games/42/")

    def test_registry_rejects_conflicting_game_type_keys(self) -> None:
        registry = GameTypeRegistry()
        registered = GameType("example", "Example", "health", "health")
        registry.register(registered)
        registry.register(registered)

        with self.assertRaisesMessage(ValueError, "already registered"):
            registry.register(GameType("example", "Other", "health", "health"))

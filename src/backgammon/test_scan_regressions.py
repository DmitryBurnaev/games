from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.utils import DatabaseError, OperationalError, ProgrammingError
from django.test import SimpleTestCase, TestCase

from .app_settings import active_setting_values
from .models import AppSetting, Game, GameMove, GamePositionSet


class RuntimeSettingsDatabaseFallbackTests(SimpleTestCase):
    """Scanner-compatible exception syntax preserves the settings fallback."""

    def test_database_errors_return_empty_overrides(self) -> None:
        for error in (DatabaseError, OperationalError, ProgrammingError):
            with self.subTest(error=error.__name__):
                with patch(
                    "backgammon.app_settings.AppSetting.objects.filter",
                    side_effect=error("database unavailable"),
                ):
                    self.assertEqual(
                        active_setting_values([AppSetting.Key.BACKGAMMON_DEBUG_TOOLS]),
                        {},
                    )

    def test_unrelated_errors_are_not_hidden(self) -> None:
        with patch(
            "backgammon.app_settings.AppSetting.objects.filter",
            side_effect=ValueError("unexpected value"),
        ):
            with self.assertRaises(ValueError):
                active_setting_values([AppSetting.Key.BACKGAMMON_DEBUG_TOOLS])


class EmptyJsonCollectionTests(TestCase):
    """Reviewed JSONField exceptions preserve empty collections, not SQL NULL."""

    def test_empty_game_and_event_collections_round_trip(self) -> None:
        player = get_user_model().objects.create_user(username="scan-regression")
        game = Game.objects.create(white_player=player)
        move = GameMove.objects.create(
            game=game, player=player, action=GameMove.Action.JOIN
        )
        position = GamePositionSet.objects.create(
            game=game, player=player, turn_number=1
        )

        game.refresh_from_db()
        move.refresh_from_db()
        position.refresh_from_db()
        for instance, field_name, expected in (
            (game, "dice", []),
            (game, "remaining_moves", []),
            (move, "metadata", {}),
            (position, "metadata", {}),
        ):
            with self.subTest(model=type(instance).__name__, field=field_name):
                field = instance._meta.get_field(field_name)
                self.assertEqual(getattr(instance, field_name), expected)
                self.assertFalse(field.null)
                field.validate(expected, instance)

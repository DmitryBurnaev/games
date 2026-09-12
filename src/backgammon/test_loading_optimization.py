from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from .models import AppSetting, Game, GameMove
from .services import serialize_game
from .views import game_state


@override_settings(BACKGAMMON_DEBUG_TOOLS=False)
class FinishedGameLoadingTests(TestCase):
    """Regression coverage for the finished-game loading projection."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.white = user_model.objects.create_user(username="white")
        self.black = user_model.objects.create_user(username="black")
        self.game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            winner=self.white,
            status=Game.Status.FINISHED,
            victory_type=Game.VictoryType.MARS,
            dice=[],
            remaining_moves=[],
            has_rolled=False,
        )
        GameMove.objects.create(
            game=self.game,
            player=self.white,
            action=GameMove.Action.ROLL,
            dice=[3, 4],
            board=self.game.board,
        )
        GameMove.objects.create(
            game=self.game,
            player=self.white,
            action=GameMove.Action.MOVE,
            dice=[3, 4],
            distance=3,
            source_point=0,
            target_point=3,
            board=self.game.board,
        )
        GameMove.objects.create(
            game=self.game,
            player=self.black,
            action=GameMove.Action.ROLL,
            dice=[2, 2],
            board=self.game.board,
        )

    def loaded_game(self) -> Game:
        """Return the same projection the state endpoint passes to serialization."""
        return Game.objects.select_related(
            "white_player",
            "black_player",
            "planned_opponent",
            "current_player",
            "winner",
        ).get(pk=self.game.pk)

    def add_history_events(self, count: int) -> None:
        """Append compact history rows without affecting the finished outcome."""
        GameMove.objects.bulk_create(
            [
                GameMove(
                    game=self.game,
                    player=self.white if index % 2 else self.black,
                    action=GameMove.Action.ROLL,
                    dice=[(index % 6) + 1, ((index + 1) % 6) + 1],
                )
                for index in range(count)
            ]
        )

    def test_finished_projection_uses_only_settings_and_history_queries(self) -> None:
        """Finished serialization has a constant two-query budget after game load."""
        game = self.loaded_game()

        with CaptureQueriesContext(connection) as queries:
            payload = serialize_game(game, self.white)

        self.assertEqual(len(queries), 2)
        query_sql = "\n".join(query["sql"] for query in queries)
        self.assertNotIn("auth_user", query_sql)
        self.assertNotIn('"backgammon_gamemove"."board"', query_sql)
        self.assertNotIn("backgammon_gamenotification", query_sql)
        self.assertEqual(payload["quick_notifications"], [])
        self.assertEqual(payload["legal_moves"], [])
        self.assertFalse(payload["can_undo"])

    def test_finished_projection_query_budget_is_constant_for_long_histories(
        self,
    ) -> None:
        """History length changes work, not the two-query finished projection budget."""
        query_counts: dict[int, int] = {}
        previous_event_count = 3
        for event_count in (10, 100, 1000):
            with self.subTest(event_count=event_count):
                self.add_history_events(event_count - previous_event_count)
                game = self.loaded_game()
                with CaptureQueriesContext(connection) as queries:
                    serialize_game(game, self.white)
                query_counts[event_count] = len(queries)
                self.assertEqual(query_counts[event_count], 2)
                previous_event_count = event_count

        self.assertEqual(query_counts, {10: 2, 100: 2, 1000: 2})

    def test_finished_projection_keeps_statistics_from_history(self) -> None:
        """The compact projection preserves dice and skipped-move statistics."""
        payload = serialize_game(self.loaded_game(), self.white)

        self.assertEqual(payload["dice_statistics"]["white"]["total_points"], 7)
        self.assertEqual(payload["dice_statistics"]["black"]["total_points"], 8)
        self.assertEqual(payload["skipped_turns"], {"white": 1, "black": 0})
        self.assertEqual(payload["skipped_points"], {"white": 4, "black": 0})

    def test_finished_projection_keeps_notification_display_setting(self) -> None:
        """An existing state field keeps its runtime-configured value when finished."""
        AppSetting.objects.update_or_create(
            key=AppSetting.Key.BACKGAMMON_NOTIFICATION_DISPLAY_MS,
            defaults={"value": "2300", "is_enabled": True},
        )

        payload = serialize_game(self.loaded_game(), self.white)

        self.assertEqual(payload["notification_display_ms"], 2300)

    def test_finished_state_endpoint_uses_three_queries_without_auth_middleware(
        self,
    ) -> None:
        """The endpoint adds only its loaded game query to the projection budget."""
        request = RequestFactory().get(f"/games/{self.game.pk}/state/")
        request.user = self.white

        with CaptureQueriesContext(connection) as queries:
            response = game_state.__wrapped__(request, self.game.pk)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(queries), 3)

    def test_finished_browser_lifecycle_waits_for_authoritative_state(self) -> None:
        """The browser never starts realtime after an initial finished response."""
        source = (
            Path(__file__)
            .with_name("static")
            .joinpath("backgammon", "game.js")
            .read_text()
        )

        self.assertIn("function disableRealtime()", source)
        self.assertIn("if (nextGame.status === 'finished')", source)
        self.assertIn("if (nextGame.status !== 'finished')", source)
        self.assertLess(
            source.index("async function initializeGame()"),
            source.index("initializeGame();"),
        )

    def test_waiting_browser_lifecycle_starts_realtime_after_initial_state(
        self,
    ) -> None:
        """Waiting games retain their WebSocket path after state initialization."""
        source = (
            Path(__file__)
            .with_name("static")
            .joinpath("backgammon", "game.js")
            .read_text()
        )

        self.assertIn(
            "if (nextGame.status !== 'finished') {\n"
            "                connectStateSocket();",
            source,
        )
        self.assertIn(
            "if (app.dataset.initialStatus !== 'finished') {\n"
            "                connectStateSocket();",
            source,
        )

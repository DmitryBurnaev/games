from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import Game, GameMove
from .services import (
    apply_move,
    create_roll,
    persist_finished_statistics,
    record_game_event,
    serialize_game,
    surrender_game,
    undo_last_move,
)


@override_settings(BACKGAMMON_DEBUG_TOOLS=True)
class SummaryDebugRegressionTests(TestCase):
    """Cached results must preserve the browser's effective debug history."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.white = user_model.objects.create_user(username="summary-white")
        self.black = user_model.objects.create_user(username="summary-black")
        self.game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            status=Game.Status.ACTIVE,
        )

    def debug_counts(self, history: list[dict]) -> dict:
        """Mirror the counters calculated from debug_move_history in game.js."""
        counts = {
            user.pk: dict(rolls=0, doubles=0, singles=0, checker_moves=0, bear_offs=0)
            for user in (self.white, self.black)
        }
        for event in history:
            player_counts = counts.get(event["player_id"])
            if player_counts is None:
                continue
            if event["action"] == GameMove.Action.ROLL and len(event["dice"]) == 2:
                player_counts["rolls"] += 1
                category = (
                    "doubles" if event["dice"][0] == event["dice"][1] else "singles"
                )
                player_counts[category] += 1
            elif event["action"] == GameMove.Action.MOVE:
                player_counts["checker_moves"] += 1
            elif event["action"] == GameMove.Action.BEAR_OFF:
                player_counts["bear_offs"] += 1
        return counts

    def finish_with_undo(self) -> None:
        with patch("backgammon.services.roll_dice_for_game", return_value=[6, 6]):
            create_roll(self.game, self.white)
        apply_move(self.game, self.white, 0, 6)
        undo_last_move(self.game, self.white)
        apply_move(self.game, self.white, 0, 6)
        surrender_game(self.game, self.white)
        self.game.refresh_from_db()

    def test_summary_matches_fallback_effective_history_and_browser_counters(
        self,
    ) -> None:
        self.finish_with_undo()
        for viewer in (self.white, self.black):
            with self.subTest(viewer=viewer.pk):
                cached = serialize_game(self.game, viewer)
                self.game.finished_dice_statistics = None
                self.game.finished_skipped_statistics = None
                fallback = serialize_game(self.game, viewer)
                self.assertEqual(cached, fallback)
                counts = self.debug_counts(cached["debug_move_history"])
                self.assertEqual(
                    counts[self.white.pk],
                    dict(rolls=1, doubles=1, singles=0, checker_moves=1, bear_offs=0),
                )
                self.assertNotIn(
                    GameMove.Action.UNDO,
                    [event["action"] for event in cached["debug_move_history"]],
                )
                self.game.refresh_from_db()
        self.assertEqual(self.game.moves.filter(action=GameMove.Action.MOVE).count(), 2)

    def test_debug_summary_loads_compact_history_without_recalculating_statistics(
        self,
    ) -> None:
        self.finish_with_undo()
        game = Game.objects.select_related(
            "white_player", "black_player", "current_player", "winner"
        ).get(pk=self.game.pk)
        with (
            patch("backgammon.services.history_statistics", side_effect=AssertionError),
            CaptureQueriesContext(connection) as queries,
        ):
            payload = serialize_game(game, self.white)
        history_queries = [
            query["sql"] for query in queries if "backgammon_gamemove" in query["sql"]
        ]
        self.assertEqual(len(history_queries), 1)
        self.assertNotIn('"board"', history_queries[0])
        self.assertNotIn("JOIN", history_queries[0])
        self.assertEqual(payload["dice_statistics"], game.finished_dice_statistics)
        self.assertTrue(payload["debug_move_history"])

    @override_settings(BACKGAMMON_DEBUG_TOOLS=False)
    def test_debug_off_summary_never_loads_history(self) -> None:
        self.finish_with_undo()
        with (
            patch("backgammon.services.game_history", side_effect=AssertionError),
            CaptureQueriesContext(connection) as queries,
        ):
            payload = serialize_game(self.game, self.white)
        self.assertNotIn("debug_move_history", payload)
        self.assertNotIn(
            "backgammon_gamemove", "\n".join(query["sql"] for query in queries)
        )

    def assert_http_summary_matches_reload_and_fallback(
        self, action_payload: dict
    ) -> None:
        self.game.refresh_from_db()
        self.assertEqual(self.game.status, Game.Status.FINISHED)
        self.assertIsInstance(self.game.finished_dice_statistics, dict)
        self.assertIsInstance(self.game.finished_skipped_statistics, dict)
        for viewer in (self.white, self.black):
            with self.subTest(viewer=viewer.pk):
                self.client.force_login(viewer)
                response = self.client.get(
                    reverse("backgammon:game_state", args=[self.game.pk])
                )
                self.assertEqual(response.status_code, 200)
                reloaded = response.json()["game"]
                if viewer == self.white:
                    self.assertEqual(action_payload, reloaded)
                cached_dice = self.game.finished_dice_statistics
                cached_skipped = self.game.finished_skipped_statistics
                self.game.finished_dice_statistics = None
                self.game.finished_skipped_statistics = None
                fallback = serialize_game(self.game, viewer)
                self.assertEqual(reloaded, fallback)
                self.game.finished_dice_statistics = cached_dice
                self.game.finished_skipped_statistics = cached_skipped

    def test_victory_response_and_reload_keep_debug_history_but_exclude_winning_roll(
        self,
    ) -> None:
        self.game.board = [None] * 24
        self.game.board[23] = {"color": Game.Color.WHITE, "count": 1}
        self.game.board[12] = {"color": Game.Color.BLACK, "count": 15}
        self.game.borne_off = {"white": 14, "black": 0}
        self.game.save()
        record_game_event(self.game, self.black, GameMove.Action.ROLL, dice=[3, 3])
        record_game_event(
            self.game, self.black, GameMove.Action.MOVE, dice=[3, 3], distance=3
        )
        with patch("backgammon.services.roll_dice_for_game", return_value=[1, 1]):
            create_roll(self.game, self.white)
        self.client.force_login(self.white)
        response = self.client.post(
            reverse("backgammon:move", args=[self.game.pk]),
            {"source": 23, "distance": 1},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()["game"]
        self.assert_http_summary_matches_reload_and_fallback(payload)
        self.assertEqual(payload["dice_statistics"]["white"]["total_points"], 0)
        self.assertEqual(payload["dice_statistics"]["white"]["double_rolls"], 0)
        self.assertEqual(payload["dice_statistics"]["black"]["total_points"], 12)
        counts = self.debug_counts(payload["debug_move_history"])
        self.assertEqual(counts[self.white.pk]["doubles"], 1)
        self.assertEqual(counts[self.white.pk]["bear_offs"], 1)

    def test_surrender_response_and_reload_keep_debug_history(self) -> None:
        with patch("backgammon.services.roll_dice_for_game", return_value=[1, 2]):
            create_roll(self.game, self.white)
        apply_move(self.game, self.white, 0, 1)
        self.client.force_login(self.white)
        response = self.client.post(
            reverse("backgammon:surrender", args=[self.game.pk]),
            {},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()["game"]
        self.assert_http_summary_matches_reload_and_fallback(payload)
        counts = self.debug_counts(payload["debug_move_history"])
        self.assertEqual(counts[self.white.pk]["singles"], 1)
        self.assertEqual(counts[self.white.pk]["checker_moves"], 1)

    def test_repopulating_summary_preserves_debug_history(self) -> None:
        self.finish_with_undo()
        original = serialize_game(self.game, self.white)
        persist_finished_statistics(self.game)
        self.game.refresh_from_db()
        self.assertEqual(serialize_game(self.game, self.white), original)

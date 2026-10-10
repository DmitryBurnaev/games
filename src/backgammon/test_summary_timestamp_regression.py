from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Game, GameMove
from .services import (
    apply_move,
    effective_game_history,
    game_history,
    history_statistics,
    persist_finished_statistics,
    record_game_event,
    surrender_game,
)


class FinishedSummaryTimestampRegressionTests(TestCase):
    """Materializing an optimization must not look like a gameplay change."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.white = user_model.objects.create_user(username="summary-white")
        self.black = user_model.objects.create_user(username="summary-black")
        self.historical_time = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)
        self.action_time = self.historical_time + timedelta(days=1)
        self.materialization_time = self.action_time + timedelta(days=1)

    def create_game(self, **fields: object) -> Game:
        """Create a game with an explicitly historical last-change timestamp."""
        game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            **fields,
        )
        Game.objects.filter(pk=game.pk).update(updated_at=self.historical_time)
        game.refresh_from_db()
        return game

    def gameplay_fields(self, game: Game) -> dict[str, object]:
        """Capture every persisted field except the two materialized summaries."""
        values = Game.objects.values().get(pk=game.pk)
        values.pop("finished_dice_statistics")
        values.pop("finished_skipped_statistics")
        return values

    def test_materialization_preserves_all_original_fields(self) -> None:
        game = self.create_game(
            status=Game.Status.FINISHED,
            winner=self.black,
            victory_type=Game.VictoryType.OIN,
            started_at=self.historical_time - timedelta(minutes=10),
            finished_at=self.historical_time,
        )
        record_game_event(game, self.white, GameMove.Action.ROLL, dice=[1, 2])
        record_game_event(game, self.white, GameMove.Action.FINISH, dice=[])
        original_fields = self.gameplay_fields(game)
        expected_dice, expected_skipped = history_statistics(
            game, effective_game_history(game_history(game))
        )

        with patch("django.utils.timezone.now", return_value=self.materialization_time):
            persist_finished_statistics(game)

        self.assertEqual(game.updated_at, self.historical_time)
        game.refresh_from_db()
        self.assertEqual(self.gameplay_fields(game), original_fields)
        self.assertEqual(game.finished_dice_statistics, expected_dice)
        self.assertEqual(game.finished_skipped_statistics, expected_skipped)
        self.assertEqual(expected_dice[Game.Color.WHITE]["total_points"], 3)

    def test_repeated_materialization_is_timestamp_and_data_stable(self) -> None:
        game = self.create_game(
            status=Game.Status.FINISHED,
            winner=self.black,
            victory_type=Game.VictoryType.OIN,
            finished_at=self.historical_time,
        )
        record_game_event(game, self.white, GameMove.Action.ROLL, dice=[6, 6])
        record_game_event(game, self.white, GameMove.Action.FINISH, dice=[])
        with patch("django.utils.timezone.now", return_value=self.action_time):
            persist_finished_statistics(game)
        first_materialization = Game.objects.values().get(pk=game.pk)
        game.refresh_from_db()

        with patch("django.utils.timezone.now", return_value=self.materialization_time):
            persist_finished_statistics(game)

        self.assertEqual(game.updated_at, self.historical_time)
        self.assertEqual(Game.objects.values().get(pk=game.pk), first_materialization)

    def assert_finished_action_timestamps(self, game: Game) -> None:
        """A real finish updates timestamps; later summary refresh does not."""
        game.refresh_from_db()
        self.assertEqual(game.status, Game.Status.FINISHED)
        self.assertEqual(game.updated_at, self.action_time)
        self.assertEqual(game.finished_at, self.action_time)
        self.assertIsInstance(game.finished_dice_statistics, dict)
        self.assertIsInstance(game.finished_skipped_statistics, dict)
        action_state = Game.objects.values().get(pk=game.pk)

        with patch("django.utils.timezone.now", return_value=self.materialization_time):
            persist_finished_statistics(game)

        game.refresh_from_db()
        self.assertEqual(Game.objects.values().get(pk=game.pk), action_state)

    def test_victory_updates_gameplay_timestamps_before_summary(self) -> None:
        board = [None] * 24
        board[23] = {"color": Game.Color.WHITE, "count": 1}
        board[12] = {"color": Game.Color.BLACK, "count": 1}
        game = self.create_game(
            status=Game.Status.ACTIVE,
            checker_count=1,
            board=board,
            dice=[1, 2],
            remaining_moves=[1, 2],
            has_rolled=True,
            started_at=self.historical_time,
        )
        record_game_event(game, self.white, GameMove.Action.ROLL, dice=[1, 2])

        with patch("django.utils.timezone.now", return_value=self.action_time):
            apply_move(game, self.white, 23, 1)

        self.assertEqual(game.winner_id, self.white.pk)
        self.assert_finished_action_timestamps(game)

    def test_surrender_updates_gameplay_timestamps_before_summary(self) -> None:
        game = self.create_game(
            status=Game.Status.ACTIVE,
            started_at=self.historical_time,
        )

        with patch("django.utils.timezone.now", return_value=self.action_time):
            surrender_game(game, self.white)

        self.assertEqual(game.winner_id, self.black.pk)
        self.assert_finished_action_timestamps(game)

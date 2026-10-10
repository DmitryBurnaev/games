from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from .models import Game, GameMove
from .services import (
    apply_move,
    create_roll,
    dice_statistics_by_color,
    double_rolls_by_color,
    finish_blocked_turn,
    serialize_game,
    skipped_statistics_by_color,
    skipped_turns_by_color,
    surrender_game,
    undo_last_move,
)


@override_settings(BACKGAMMON_DEBUG_TOOLS=False)
class StatisticsHelperRegressionTests(TestCase):
    """Standalone helpers must use the same effective events as public state."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.white = user_model.objects.create_user(username="statistics-white")
        self.black = user_model.objects.create_user(username="statistics-black")

    def make_game(
        self, white_point: int, black_point: int, checker_count: int = 15
    ) -> Game:
        board = [None] * 24
        board[white_point] = {"color": Game.Color.WHITE, "count": checker_count}
        board[black_point] = {"color": Game.Color.BLACK, "count": checker_count}
        return Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            status=Game.Status.ACTIVE,
            checker_count=checker_count,
            board=board,
        )

    def roll(self, game: Game, player, dice: list[int]) -> None:
        with patch("backgammon.services.roll_dice_for_game", return_value=dice):
            self.assertEqual(create_roll(game, player), dice)

    def assert_helpers_match_state(self, game: Game) -> None:
        dice_statistics = dice_statistics_by_color(game)
        skipped_statistics = skipped_statistics_by_color(game)
        for viewer in (self.white, self.black):
            with self.subTest(viewer_id=viewer.pk, status=game.status):
                payload = serialize_game(game, viewer)
                self.assertEqual(dice_statistics, payload["dice_statistics"])
                self.assertEqual(double_rolls_by_color(game), payload["double_rolls"])
                self.assertEqual(skipped_turns_by_color(game), payload["skipped_turns"])
                for name in ("moves", "points"):
                    self.assertEqual(
                        {
                            color: values[name]
                            for color, values in skipped_statistics.items()
                        },
                        payload[f"skipped_{name}"],
                    )

    def test_double_usage_excludes_undone_move_after_replay_and_turn_finish(
        self,
    ) -> None:
        game = self.make_game(1, 12)
        self.roll(game, self.white, [6, 6])
        apply_move(game, self.white, 1, 6)
        apply_move(game, self.white, 7, 6)
        undo_last_move(game, self.white)

        self.assertEqual(
            dice_statistics_by_color(game)["white"]["double_moves_used"], 1
        )
        self.assert_helpers_match_state(game)

        apply_move(game, self.white, 7, 6)
        self.assertEqual(
            dice_statistics_by_color(game)["white"]["double_moves_used"], 2
        )
        apply_move(game, self.white, 13, 6)
        apply_move(game, self.white, 1, 6)
        finish_blocked_turn(game, self.white)
        self.roll(game, self.black, [1, 2])

        self.assertEqual(game.moves.filter(action=GameMove.Action.MOVE).count(), 5)
        self.assertEqual(
            dice_statistics_by_color(game)["white"]["double_moves_used"], 4
        )
        self.assertEqual(double_rolls_by_color(game), {"white": 1, "black": 0})
        self.assertEqual(skipped_statistics_by_color(game)["white"]["moves"], 0)
        self.assert_helpers_match_state(game)

    def test_ordinary_undo_restores_skipped_die_when_next_roll_closes_turn(
        self,
    ) -> None:
        game = self.make_game(1, 4, checker_count=1)
        self.roll(game, self.white, [1, 2])
        apply_move(game, self.white, 1, 2)
        undo_last_move(game, self.white)
        apply_move(game, self.white, 1, 1)
        finish_blocked_turn(game, self.white)
        self.roll(game, self.black, [3, 4])

        self.assertEqual(
            skipped_statistics_by_color(game)["white"],
            {"turns": 1, "moves": 1, "points": 2},
        )
        self.assertEqual(skipped_turns_by_color(game), {"white": 1, "black": 0})
        self.assert_helpers_match_state(game)
        surrender_game(game, self.black)
        game.refresh_from_db()
        self.assert_helpers_match_state(game)

    def test_bear_off_undo_is_excluded_from_double_usage_and_finished_summary(
        self,
    ) -> None:
        game = self.make_game(23, 12, checker_count=2)
        self.roll(game, self.white, [1, 1])
        apply_move(game, self.white, 23, 1)
        undo_last_move(game, self.white)

        self.assertEqual(game.borne_off["white"], 0)
        self.assertEqual(
            dice_statistics_by_color(game)["white"]["double_moves_used"], 0
        )
        self.assert_helpers_match_state(game)
        apply_move(game, self.white, 23, 1)
        surrender_game(game, self.white)
        game.refresh_from_db()

        self.assertEqual(
            dice_statistics_by_color(game)["white"]["double_moves_used"], 1
        )
        self.assert_helpers_match_state(game)

    def test_winning_roll_is_excluded_after_bear_off_undo_and_replay(self) -> None:
        game = self.make_game(23, 12, checker_count=2)
        self.roll(game, self.white, [1, 1])
        apply_move(game, self.white, 23, 1)
        undo_last_move(game, self.white)
        apply_move(game, self.white, 23, 1)
        apply_move(game, self.white, 23, 1)
        game.refresh_from_db()

        self.assertEqual(game.status, Game.Status.FINISHED)
        self.assertEqual(
            dice_statistics_by_color(game)["white"],
            {
                "total_points": 0,
                "double_rolls": 0,
                "double_moves_used": 0,
                "double_moves_available": 0,
            },
        )
        self.assertEqual(
            skipped_statistics_by_color(game)["white"],
            {"turns": 0, "moves": 0, "points": 0},
        )
        self.assert_helpers_match_state(game)
        game.finished_dice_statistics = None
        game.finished_skipped_statistics = None
        self.assert_helpers_match_state(game)

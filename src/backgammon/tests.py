from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Game, PlayerStats
from .services import (
    GameError,
    apply_move,
    create_roll,
    finish_blocked_turn,
    serialize_game,
    undo_last_move,
)


class GameRulesTests(TestCase):
    """Regression coverage for long-backgammon game rules and turn flow."""

    def setUp(self) -> None:
        """Create two users for each test case."""
        User = get_user_model()
        self.white = User.objects.create_user(username="white", password="pass")
        self.black = User.objects.create_user(username="black", password="pass")

    def active_game(self) -> Game:
        """Create a minimal active game with white to move."""
        return Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            status=Game.Status.ACTIVE,
            dice=[1, 2],
            remaining_moves=[1, 2],
            has_rolled=True,
        )

    def test_player_cannot_land_on_opponent_point(self) -> None:
        """A checker cannot move onto a point occupied by the opponent."""
        game = self.active_game()
        game.board = [None for _ in range(24)]
        game.board[0] = {"color": Game.Color.WHITE, "count": 1}
        game.board[3] = {"color": Game.Color.BLACK, "count": 1}
        game.dice = [3]
        game.remaining_moves = [3]
        game.save()

        with self.assertRaisesMessage(GameError, "занятый соперником"):
            apply_move(game, self.white, 0, 3)

    def test_only_one_checker_can_leave_head_during_regular_turn(self) -> None:
        """A regular turn allows only one checker to leave the head."""
        game = self.active_game()

        apply_move(game, self.white, 0, 1)

        with self.assertRaisesMessage(GameError, "только одну шашку"):
            apply_move(game, self.white, 0, 2)

    def test_first_turn_double_3_4_6_can_take_two_from_head(self) -> None:
        """First-turn doubles 3, 4, and 6 allow two head moves."""
        game = self.active_game()
        game.dice = [4, 4]
        game.remaining_moves = [4, 4, 4, 4]
        game.turn_number = 1
        game.save()

        apply_move(game, self.white, 0, 4)
        apply_move(game, self.white, 0, 4)

        self.assertEqual(game.head_moves_this_turn, 2)
        with self.assertRaisesMessage(GameError, "только одну шашку"):
            apply_move(game, self.white, 0, 4)

    def test_bear_off_last_checker_finishes_game_and_updates_stats(self) -> None:
        """Bearing off the fifteenth checker finishes the game and stats."""
        game = self.active_game()
        game.board = [None for _ in range(24)]
        game.board[23] = {"color": Game.Color.WHITE, "count": 1}
        game.borne_off = {Game.Color.WHITE: 14, Game.Color.BLACK: 0}
        game.dice = [1]
        game.remaining_moves = [1]
        game.save()

        apply_move(game, self.white, 23, 1)
        game.refresh_from_db()

        self.assertEqual(game.status, Game.Status.FINISHED)
        self.assertEqual(game.winner, self.white)
        self.assertEqual(game.victory_type, Game.VictoryType.MARS)
        self.assertEqual(PlayerStats.objects.get(user=self.white).wins, 1)
        self.assertEqual(PlayerStats.objects.get(user=self.black).losses, 1)

    def test_undo_last_move_restores_checker_and_dice(self) -> None:
        """Undo restores the board, available dice move, and head counter."""
        game = self.active_game()

        apply_move(game, self.white, 0, 1)
        undo_last_move(game, self.white)
        game.refresh_from_db()

        self.assertEqual(game.board[0], {"color": Game.Color.WHITE, "count": 15})
        self.assertIsNone(game.board[1])
        self.assertCountEqual(game.remaining_moves, [1, 2])
        self.assertEqual(game.current_player, self.white)
        self.assertEqual(game.head_moves_this_turn, 0)

    def test_last_move_waits_for_explicit_finish_and_allows_undo(self) -> None:
        """The last checker move keeps the turn open until explicit finish."""
        game = self.active_game()
        game.dice = [1]
        game.remaining_moves = [1]
        game.save()

        apply_move(game, self.white, 0, 1)
        game.refresh_from_db()

        self.assertEqual(game.current_player, self.white)
        self.assertTrue(game.has_rolled)
        self.assertEqual(game.remaining_moves, [])

        payload = serialize_game(game, self.white)
        self.assertTrue(payload["can_end_turn"])
        self.assertTrue(payload["can_undo"])
        self.assertFalse(payload["can_roll"])

    def test_undo_is_disabled_after_finishing_turn(self) -> None:
        """Undo and controls are disabled once the player ends their turn."""
        game = self.active_game()
        game.dice = [1]
        game.remaining_moves = [1]
        game.save()

        apply_move(game, self.white, 0, 1)
        finish_blocked_turn(game, self.white)
        game.refresh_from_db()

        self.assertEqual(game.current_player, self.black)

        payload = serialize_game(game, self.white)
        self.assertFalse(payload["can_roll"])
        self.assertFalse(payload["can_end_turn"])
        self.assertFalse(payload["can_undo"])
        self.assertEqual(payload["legal_moves"], [])

        with self.assertRaisesMessage(GameError, "до завершения"):
            undo_last_move(game, self.white)

    def test_move_markers_live_until_opponent_rolls(self) -> None:
        """Moved-checker markers transfer to the opponent until their roll."""
        game = self.active_game()
        game.board = [None for _ in range(24)]
        game.board[0] = {"color": Game.Color.WHITE, "count": 14}
        game.board[5] = {"color": Game.Color.WHITE, "count": 1}
        game.board[12] = {"color": Game.Color.BLACK, "count": 15}
        game.dice = [1, 2]
        game.remaining_moves = [1, 2]
        game.save()

        apply_move(game, self.white, 0, 1)
        apply_move(game, self.white, 5, 2)
        game.refresh_from_db()

        white_payload = serialize_game(game, self.white)
        self.assertEqual(
            {
                marker["target"]: marker["count"]
                for marker in white_payload["last_move_markers"]
            },
            {1: 1, 7: 1},
        )

        finish_blocked_turn(game, self.white)
        game.refresh_from_db()

        white_payload = serialize_game(game, self.white)
        black_payload = serialize_game(game, self.black)
        self.assertIsNone(white_payload["last_move_marker"])
        self.assertEqual(white_payload["last_move_markers"], [])
        self.assertEqual(
            {
                marker["target"]: marker["count"]
                for marker in black_payload["last_move_markers"]
            },
            {1: 1, 7: 1},
        )

        create_roll(game, self.black)
        game.refresh_from_db()

        black_payload = serialize_game(game, self.black)
        self.assertIsNone(black_payload["last_move_marker"])
        self.assertEqual(black_payload["last_move_markers"], [])

    def test_move_markers_count_multiple_checkers_on_same_point(self) -> None:
        """Markers count multiple moved checkers landing on one point."""
        game = self.active_game()
        game.board = [None for _ in range(24)]
        game.board[0] = {"color": Game.Color.WHITE, "count": 14}
        game.board[1] = {"color": Game.Color.WHITE, "count": 1}
        game.board[12] = {"color": Game.Color.BLACK, "count": 15}
        game.dice = [1, 2]
        game.remaining_moves = [1, 2]
        game.save()

        apply_move(game, self.white, 0, 2)
        apply_move(game, self.white, 1, 1)
        game.refresh_from_db()

        white_payload = serialize_game(game, self.white)
        self.assertEqual(len(white_payload["last_move_markers"]), 1)
        self.assertEqual(white_payload["last_move_markers"][0]["target"], 2)
        self.assertEqual(white_payload["last_move_markers"][0]["count"], 2)

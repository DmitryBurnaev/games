import json
from datetime import datetime, timezone as datetime_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Game, GameMove, PlayerStats
from .services import (
    GameError,
    apply_move,
    arrange_blocking_event_test,
    arrange_checkers_for_victory_test,
    arrange_checkers_in_home,
    create_roll,
    finish_blocked_turn,
    roll_dice_from_player_bag,
    serialize_game,
    undo_last_move,
)


class UserRegistrationTests(TestCase):
    """Coverage for the public signup gate."""

    @override_settings(ALLOW_USER_REGISTRATION=True)
    def test_signup_creates_user_when_registration_is_open(self) -> None:
        """The signup view creates and logs in a user when enabled."""
        User = get_user_model()

        response = self.client.post(
            reverse("backgammon:signup"),
            {
                "username": "new-player",
                "password1": "VeryStrongPassword123!",
                "password2": "VeryStrongPassword123!",
            },
        )

        self.assertRedirects(response, reverse("backgammon:game_list"))
        self.assertTrue(User.objects.filter(username="new-player").exists())

    @override_settings(ALLOW_USER_REGISTRATION=False)
    def test_signup_does_not_create_user_when_registration_is_closed(self) -> None:
        """The signup view rejects direct POSTs when registration is disabled."""
        User = get_user_model()

        response = self.client.post(
            reverse("backgammon:signup"),
            {
                "username": "new-player",
                "password1": "VeryStrongPassword123!",
                "password2": "VeryStrongPassword123!",
            },
        )

        self.assertRedirects(response, reverse("login"))
        self.assertFalse(User.objects.filter(username="new-player").exists())

    @override_settings(ALLOW_USER_REGISTRATION=False)
    def test_login_page_hides_signup_link_when_registration_is_closed(self) -> None:
        """The login template does not advertise disabled signup."""
        response = self.client.get(reverse("login"))

        self.assertNotContains(response, "Создать аккаунт")

    @override_settings(ALLOW_USER_REGISTRATION=True)
    def test_login_page_shows_signup_link_when_registration_is_open(self) -> None:
        """The login template advertises signup when the feature is enabled."""
        response = self.client.get(reverse("login"))

        self.assertContains(response, "Создать аккаунт")


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

    def test_first_turn_can_take_extra_from_head_when_no_other_moves_exist(
        self,
    ) -> None:
        """A blocked first turn allows one extra checker to leave the head."""
        game = self.active_game()
        game.board = [None for _ in range(24)]
        game.board[0] = {"color": Game.Color.WHITE, "count": 15}
        game.board[10] = {"color": Game.Color.BLACK, "count": 1}
        game.board[12] = {"color": Game.Color.BLACK, "count": 14}
        game.dice = [5, 5]
        game.remaining_moves = [5, 5, 5, 5]
        game.save()

        apply_move(game, self.white, 0, 5)
        game.refresh_from_db()

        payload = serialize_game(game, self.white)
        self.assertIn(
            {"source": 0, "target": 5},
            [
                {"source": move["source"], "target": move["target"]}
                for move in payload["legal_moves"]
            ],
        )

        apply_move(game, self.white, 0, 5)
        game.refresh_from_db()

        self.assertEqual(game.head_moves_this_turn, 2)

    def test_extra_head_move_is_blocked_when_non_head_move_exists(self) -> None:
        """The emergency head move is unavailable while another move is legal."""
        game = self.active_game()
        game.dice = [5, 5]
        game.remaining_moves = [5, 5, 5, 5]
        game.save()

        apply_move(game, self.white, 0, 5)

        with self.assertRaisesMessage(GameError, "только одну шашку"):
            apply_move(game, self.white, 0, 5)

    def test_black_first_turn_can_take_extra_from_head_after_white_started(
        self,
    ) -> None:
        """The extra head rule is tied to the color's first turn, not game turn one."""
        game = self.active_game()
        game.current_player = self.black
        game.board = [None for _ in range(24)]
        game.board[0] = {"color": Game.Color.WHITE, "count": 14}
        game.board[12] = {"color": Game.Color.BLACK, "count": 15}
        game.board[22] = {"color": Game.Color.WHITE, "count": 1}
        game.dice = [5, 5]
        game.remaining_moves = [5, 5, 5, 5]
        game.turn_number = 2
        game.save()
        apply_move(game, self.black, 12, 5)
        game.refresh_from_db()

        apply_move(game, self.black, 12, 5)
        game.refresh_from_db()

        self.assertEqual(game.head_moves_this_turn, 2)

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

    def test_blocking_event_blocks_turn_finish_until_position_changes(self) -> None:
        """A six-block without an opponent ahead must be broken before finish."""
        game = self.active_game()
        arrange_blocking_event_test(game, self.white)
        game.refresh_from_db()

        payload = serialize_game(game, self.white)
        self.assertTrue(payload["blocking_event"])
        self.assertEqual(payload["blocking_event_points"], [0, 1, 2, 3, 4, 5])
        self.assertFalse(payload["can_end_turn"])
        self.assertEqual(payload["legal_moves"], [])

        with self.assertRaisesMessage(GameError, "разбейте блок"):
            finish_blocked_turn(game, self.white)

        undo_last_move(game, self.white)
        game.refresh_from_db()
        apply_move(game, self.white, 10, 1)
        game.refresh_from_db()

        payload = serialize_game(game, self.white)
        self.assertFalse(payload["blocking_event"])
        self.assertTrue(payload["can_end_turn"])

        finish_blocked_turn(game, self.white)
        game.refresh_from_db()
        self.assertEqual(game.current_player, self.black)

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

    def test_first_roll_sets_game_started_at(self) -> None:
        """The game records the real start time on the first dice roll."""
        game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            status=Game.Status.ACTIVE,
        )

        create_roll(game, self.white)
        game.refresh_from_db()

        self.assertIsNotNone(game.started_at)

        first_started_at = game.started_at
        game.dice = []
        game.remaining_moves = []
        game.has_rolled = False
        game.save()
        create_roll(game, self.white)
        game.refresh_from_db()

        self.assertEqual(game.started_at, first_started_at)

    @patch("backgammon.services.roll_dice", return_value=[6, 6])
    def test_create_roll_uses_central_dice_generator(self, mocked_roll) -> None:
        """Roll creation persists dice from the shared server-side generator."""
        game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            status=Game.Status.ACTIVE,
        )

        dice = create_roll(game, self.white)
        game.refresh_from_db()

        mocked_roll.assert_called_once_with()
        self.assertEqual(dice, [6, 6])
        self.assertEqual(game.dice, [6, 6])
        self.assertEqual(game.remaining_moves, [6, 6, 6, 6])
        self.assertTrue(game.has_rolled)
        self.assertTrue(
            GameMove.objects.filter(
                game=game,
                player=self.white,
                action=GameMove.Action.ROLL,
                dice=[6, 6],
            ).exists()
        )

    @patch("backgammon.services.secrets.choice")
    def test_player_bag_dice_mode_uses_remaining_pairs(self, mocked_choice) -> None:
        """The player-bag dice mode avoids pairs already used in this cycle."""
        game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            status=Game.Status.ACTIVE,
        )
        GameMove.objects.create(
            game=game,
            player=self.white,
            action=GameMove.Action.ROLL,
            dice=[1, 1],
        )
        GameMove.objects.create(
            game=game,
            player=self.white,
            action=GameMove.Action.ROLL,
            dice=[1, 2],
        )

        def choose_remaining(remaining):
            self.assertNotIn((1, 1), remaining)
            self.assertNotIn((1, 2), remaining)
            return (6, 6)

        mocked_choice.side_effect = choose_remaining

        self.assertEqual(roll_dice_from_player_bag(game, self.white), [6, 6])

    @patch("backgammon.services.secrets.choice")
    def test_player_bag_dice_mode_resets_after_full_cycle(self, mocked_choice) -> None:
        """After 36 personal rolls the player's dice bag starts a fresh cycle."""
        game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            status=Game.Status.ACTIVE,
        )
        for left in range(1, 7):
            for right in range(1, 7):
                GameMove.objects.create(
                    game=game,
                    player=self.white,
                    action=GameMove.Action.ROLL,
                    dice=[left, right],
                )

        def choose_from_fresh_bag(remaining):
            self.assertEqual(len(remaining), 36)
            self.assertIn((1, 1), remaining)
            return (1, 1)

        mocked_choice.side_effect = choose_from_fresh_bag

        self.assertEqual(roll_dice_from_player_bag(game, self.white), [1, 1])

    @override_settings(BACKGAMMON_DICE_MODE="player_bag")
    @patch("backgammon.services.roll_dice_from_player_bag", return_value=[3, 3])
    def test_create_roll_can_use_player_bag_dice_mode(self, mocked_roll) -> None:
        """Roll creation can use the balanced per-player dice mode."""
        game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            status=Game.Status.ACTIVE,
        )

        dice = create_roll(game, self.white)
        game.refresh_from_db()

        mocked_roll.assert_called_once_with(game, self.white)
        self.assertEqual(dice, [3, 3])
        self.assertEqual(game.dice, [3, 3])
        self.assertEqual(game.remaining_moves, [3, 3, 3, 3])

    def test_serialized_game_includes_timestamps_and_double_roll_counts(self) -> None:
        """The detail UI receives final-game stats for rendering."""
        game = self.active_game()
        game.started_at = datetime(2026, 5, 3, 10, 5, tzinfo=datetime_timezone.utc)
        game.finished_at = datetime(2026, 5, 3, 11, 23, tzinfo=datetime_timezone.utc)
        game.save()
        GameMove.objects.create(
            game=game,
            player=self.white,
            action=GameMove.Action.ROLL,
            dice=[6, 6],
        )
        GameMove.objects.create(
            game=game,
            player=self.white,
            action=GameMove.Action.ROLL,
            dice=[1, 2],
        )
        GameMove.objects.create(
            game=game,
            player=self.black,
            action=GameMove.Action.ROLL,
            dice=[3, 3],
        )

        payload = serialize_game(game, self.white)

        self.assertEqual(payload["started_at"], "2026-05-03T10:05:00+00:00")
        self.assertEqual(payload["finished_at"], "2026-05-03T11:23:00+00:00")
        self.assertEqual(payload["double_rolls"], {"white": 1, "black": 1})

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
        self.assertEqual(
            [
                (move["source"], move["target"])
                for move in white_payload["last_move_steps"]
            ],
            [(0, 2), (1, 2)],
        )
        self.assertTrue(all(move["id"] for move in white_payload["last_move_steps"]))

    def test_arrange_checkers_in_home_prepares_bear_off_testing(self) -> None:
        """The finish-test helper moves only the user into home and resets turn."""
        game = self.active_game()
        game.current_player = self.black
        game.dice = [6, 6]
        game.remaining_moves = [6, 6, 6, 6]
        game.has_rolled = True
        game.save()

        arrange_checkers_in_home(game, self.white)
        game.refresh_from_db()

        white_points = [
            point
            for point, stack in enumerate(game.board)
            if stack and stack["color"] == Game.Color.WHITE
        ]
        self.assertEqual(game.current_player, self.white)
        self.assertFalse(game.has_rolled)
        self.assertEqual(game.dice, [])
        self.assertEqual(game.remaining_moves, [])
        self.assertTrue(all(18 <= point <= 23 for point in white_points))
        self.assertEqual(
            sum(game.board[point]["count"] for point in white_points),
            15,
        )
        self.assertEqual(game.board[12], {"color": Game.Color.BLACK, "count": 15})

    def test_arrange_checkers_for_victory_test_leaves_two_to_bear_off(self) -> None:
        """The victory-test helper leaves two checkers and thirteen borne off."""
        game = self.active_game()

        arrange_checkers_for_victory_test(game, self.white)
        game.refresh_from_db()

        white_points = [
            point
            for point, stack in enumerate(game.board)
            if stack and stack["color"] == Game.Color.WHITE
        ]
        self.assertEqual(game.borne_off[Game.Color.WHITE], 13)
        self.assertEqual(game.current_player, self.white)
        self.assertEqual(game.dice, [])
        self.assertEqual(sum(game.board[point]["count"] for point in white_points), 2)
        self.assertEqual(set(white_points), {22, 23})

    def test_victory_test_position_can_finish_game(self) -> None:
        """The victory-test position can immediately exercise final scoring."""
        game = self.active_game()
        arrange_checkers_for_victory_test(game, self.white)
        game.dice = [1, 2]
        game.remaining_moves = [1, 2]
        game.has_rolled = True
        game.save()

        apply_move(game, self.white, 23, 1)
        apply_move(game, self.white, 22, 2)
        game.refresh_from_db()

        self.assertEqual(game.status, Game.Status.FINISHED)
        self.assertEqual(game.winner, self.white)

    def test_surrender_finishes_game_for_opponent_and_updates_stats(self) -> None:
        """A player may resign and give a regular win to the opponent."""
        game = self.active_game()
        self.client.force_login(self.white)

        response = self.client.post(
            reverse("backgammon:surrender", args=[game.pk]),
            data="{}",
            content_type="application/json",
        )
        game.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(game.status, Game.Status.FINISHED)
        self.assertEqual(game.winner, self.black)
        self.assertEqual(game.victory_type, Game.VictoryType.OIN)
        self.assertFalse(response.json()["game"]["can_surrender"])
        self.assertEqual(PlayerStats.objects.get(user=self.black).wins, 1)
        self.assertEqual(PlayerStats.objects.get(user=self.white).losses, 1)

    def test_surrender_can_mark_mars_for_opponent_block_outside_home(self) -> None:
        """A surrender may be marked as mars when the opponent has an outside block."""
        game = self.active_game()
        game.board = [None for _ in range(24)]
        for point in range(12, 18):
            game.board[point] = {"color": Game.Color.BLACK, "count": 1}
        game.board[0] = {"color": Game.Color.WHITE, "count": 1}
        game.save()
        self.client.force_login(self.white)

        response = self.client.post(
            reverse("backgammon:surrender", args=[game.pk]),
            data=json.dumps({"victory_type": "mars"}),
            content_type="application/json",
        )
        game.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(game.status, Game.Status.FINISHED)
        self.assertEqual(game.winner, self.black)
        self.assertEqual(game.victory_type, Game.VictoryType.MARS)
        self.assertEqual(PlayerStats.objects.get(user=self.black).mars_wins, 1)
        self.assertEqual(PlayerStats.objects.get(user=self.white).mars_losses, 1)

    def test_surrender_rejects_mars_for_block_in_opponent_home(self) -> None:
        """A six-point block in the opponent's home does not allow mars surrender."""
        game = self.active_game()
        game.board = [None for _ in range(24)]
        for point in range(6, 12):
            game.board[point] = {"color": Game.Color.BLACK, "count": 1}
        game.board[0] = {"color": Game.Color.WHITE, "count": 1}
        game.save()
        self.client.force_login(self.white)

        response = self.client.post(
            reverse("backgammon:surrender", args=[game.pk]),
            data=json.dumps({"victory_type": "mars"}),
            content_type="application/json",
        )
        game.refresh_from_db()

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(game.status, Game.Status.ACTIVE)

    def test_serialized_game_exposes_surrender_state(self) -> None:
        """The browser receives surrender availability and mars suggestion state."""
        game = self.active_game()
        game.board = [None for _ in range(24)]
        for point in range(12, 18):
            game.board[point] = {"color": Game.Color.BLACK, "count": 1}
        game.board[0] = {"color": Game.Color.WHITE, "count": 1}
        game.save()

        payload = serialize_game(game, self.white)

        self.assertTrue(payload["can_surrender"])
        self.assertTrue(payload["surrender_mars_available"])


class GameLobbyTests(TestCase):
    """Coverage for lobby game metadata."""

    def setUp(self) -> None:
        """Create users and log in the viewer."""
        User = get_user_model()
        self.viewer = User.objects.create_user(username="viewer", password="pass")
        self.opponent = User.objects.create_user(username="opponent", password="pass")
        self.client.force_login(self.viewer)

    def test_lobby_shows_start_duration_and_winner_result(self) -> None:
        """Finished games show duration and color the winner relative to the viewer."""
        viewer_win = Game.objects.create(
            white_player=self.viewer,
            black_player=self.opponent,
            winner=self.viewer,
            status=Game.Status.FINISHED,
            victory_type=Game.VictoryType.OIN,
        )
        opponent_win = Game.objects.create(
            white_player=self.viewer,
            black_player=self.opponent,
            winner=self.opponent,
            status=Game.Status.FINISHED,
            victory_type=Game.VictoryType.MARS,
        )
        Game.objects.create(
            white_player=self.viewer,
            black_player=self.opponent,
            current_player=self.viewer,
            status=Game.Status.ACTIVE,
        )
        Game.objects.create(white_player=self.viewer, status=Game.Status.WAITING)
        Game.objects.filter(pk=viewer_win.pk).update(
            created_at=datetime(2026, 5, 3, 10, 0, tzinfo=datetime_timezone.utc),
            started_at=datetime(2026, 5, 3, 10, 5, tzinfo=datetime_timezone.utc),
            finished_at=datetime(2026, 5, 3, 11, 23, tzinfo=datetime_timezone.utc),
        )
        Game.objects.filter(pk=opponent_win.pk).update(
            created_at=datetime(2026, 5, 3, 12, 0, tzinfo=datetime_timezone.utc),
            started_at=datetime(2026, 5, 3, 12, 1, tzinfo=datetime_timezone.utc),
            finished_at=datetime(2026, 5, 3, 12, 5, tzinfo=datetime_timezone.utc),
        )
        GameMove.objects.create(
            game=viewer_win,
            player=self.viewer,
            action=GameMove.Action.ROLL,
            dice=[6, 6],
        )
        GameMove.objects.create(
            game=viewer_win,
            player=self.opponent,
            action=GameMove.Action.ROLL,
            dice=[2, 3],
        )

        response = self.client.get(reverse("backgammon:game_list"))

        self.assertContains(response, "03.05.2026 13:05")
        self.assertNotContains(response, "03.05.2026 10:05")
        self.assertNotContains(response, "🗓️")
        self.assertContains(response, "⌛ 1 час 18 мин")
        self.assertNotContains(response, "дубли:")
        self.assertContains(response, "viewer ⇆ opponent")
        self.assertContains(response, "viewer ⇆ opponent 🌚")
        self.assertContains(response, "победа")
        self.assertContains(response, "text-bg-success")
        self.assertContains(response, "поражение")
        self.assertContains(response, "🌚")
        self.assertNotContains(response, "поражение 🌚")
        self.assertContains(response, "text-bg-danger")
        self.assertContains(response, "игра в процессе ...")
        self.assertContains(response, "ожидание соперника")
        self.assertNotContains(response, "Waiting for opponent")
        self.assertNotContains(response, "Победил:")
        self.assertNotContains(response, "Active")
        self.assertNotContains(response, "Finished")


class GameDebugToolsTests(TestCase):
    """Coverage for debug-only game helper controls and endpoints."""

    def setUp(self) -> None:
        """Create an active game and log in the white player."""
        User = get_user_model()
        self.white = User.objects.create_user(username="white", password="pass")
        self.black = User.objects.create_user(username="black", password="pass")
        self.game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            status=Game.Status.ACTIVE,
        )
        self.client.force_login(self.white)

    @override_settings(BACKGAMMON_DEBUG_TOOLS=True)
    def test_debug_buttons_render_when_enabled(self) -> None:
        """Debug buttons are visible when the backend setting enables them."""
        response = self.client.get(
            reverse("backgammon:game_detail", kwargs={"pk": self.game.pk})
        )

        self.assertContains(response, "В дом для теста")
        self.assertContains(response, "Тест победы")
        self.assertContains(response, "Тест головы 5/5")
        self.assertContains(response, "Тест блока 6")
        self.assertContains(response, "👈")
        self.assertContains(response, 'data-debug-tools="1"')
        self.assertContains(response, "data-prepare-bear-off-url")
        self.assertContains(response, "data-prepare-extra-head-move-url")
        self.assertContains(response, "data-prepare-blocking-event-url")

    @override_settings(BACKGAMMON_ANIMATIONS_ENABLED=True)
    def test_animation_flag_renders_when_enabled(self) -> None:
        """The frontend receives the checker animation feature flag."""
        response = self.client.get(
            reverse("backgammon:game_detail", kwargs={"pk": self.game.pk})
        )

        self.assertContains(response, 'data-animations-enabled="1"')

    @override_settings(BACKGAMMON_ANIMATIONS_ENABLED=False)
    def test_animation_flag_renders_when_disabled(self) -> None:
        """Checker animations can be disabled through settings."""
        response = self.client.get(
            reverse("backgammon:game_detail", kwargs={"pk": self.game.pk})
        )

        self.assertContains(response, 'data-animations-enabled="0"')

    @override_settings(BACKGAMMON_POLL_INTERVAL_MS=750)
    def test_poll_interval_setting_renders_for_frontend(self) -> None:
        """The frontend receives the configured state polling interval."""
        response = self.client.get(
            reverse("backgammon:game_detail", kwargs={"pk": self.game.pk})
        )

        self.assertContains(response, 'data-poll-interval-ms="750"')

    def test_finished_game_hides_control_buttons_initially(self) -> None:
        """Finished game pages render controls hidden before frontend state loads."""
        self.game.mark_finished(self.white, Game.VictoryType.OIN)
        self.game.save()

        response = self.client.get(
            reverse("backgammon:game_detail", kwargs={"pk": self.game.pk})
        )

        self.assertContains(
            response, 'id="control-panel" class="d-grid gap-2 mb-3 d-none"'
        )
        self.assertContains(
            response,
            'id="surrender-button" class="btn btn-outline-danger btn-sm surrender-button d-none"',
        )

    @override_settings(BACKGAMMON_DEBUG_TOOLS=True)
    def test_extra_head_debug_helper_prepares_blocked_first_turn(self) -> None:
        """The debug endpoint prepares a 5/5 first-turn extra-head scenario."""
        response = self.client.post(
            reverse("backgammon:prepare_extra_head_move", kwargs={"pk": self.game.pk})
        )
        self.game.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(self.game.dice, [5, 5])
        self.assertEqual(self.game.remaining_moves, [5, 5, 5, 5])
        self.assertEqual(self.game.current_player, self.white)

        apply_move(self.game, self.white, 0, 5)
        self.game.refresh_from_db()

        payload = serialize_game(self.game, self.white)
        self.assertIn(
            {"source": 0, "target": 5},
            [
                {"source": move["source"], "target": move["target"]}
                for move in payload["legal_moves"]
            ],
        )

    @override_settings(BACKGAMMON_DEBUG_TOOLS=True)
    def test_blocking_event_debug_helper_blocks_turn_finish(self) -> None:
        """The debug endpoint prepares a position blocked by the six-block rule."""
        response = self.client.post(
            reverse("backgammon:prepare_blocking_event", kwargs={"pk": self.game.pk})
        )
        self.game.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(self.game.dice, [1])
        self.assertEqual(self.game.remaining_moves, [])
        self.assertEqual(self.game.current_player, self.white)

        payload = serialize_game(self.game, self.white)
        self.assertTrue(payload["blocking_event"])
        self.assertFalse(payload["can_end_turn"])
        self.assertTrue(payload["can_undo"])

    @override_settings(BACKGAMMON_DEBUG_TOOLS=False)
    def test_debug_buttons_and_endpoint_are_disabled_when_setting_is_off(self) -> None:
        """Debug buttons are hidden and helper endpoints reject requests."""
        detail_response = self.client.get(
            reverse("backgammon:game_detail", kwargs={"pk": self.game.pk})
        )
        endpoint_response = self.client.post(
            reverse("backgammon:prepare_bear_off", kwargs={"pk": self.game.pk})
        )
        extra_head_response = self.client.post(
            reverse("backgammon:prepare_extra_head_move", kwargs={"pk": self.game.pk})
        )
        blocking_event_response = self.client.post(
            reverse("backgammon:prepare_blocking_event", kwargs={"pk": self.game.pk})
        )

        self.assertNotContains(detail_response, "В дом для теста")
        self.assertNotContains(detail_response, "Тест победы")
        self.assertNotContains(detail_response, "Тест головы 5/5")
        self.assertNotContains(detail_response, "Тест блока 6")
        self.assertContains(detail_response, 'data-debug-tools="0"')
        self.assertNotContains(detail_response, "data-prepare-bear-off-url")
        self.assertNotContains(detail_response, "data-prepare-extra-head-move-url")
        self.assertNotContains(detail_response, "data-prepare-blocking-event-url")
        self.assertEqual(endpoint_response.status_code, 403)
        self.assertEqual(extra_head_response.status_code, 403)
        self.assertEqual(blocking_event_response.status_code, 403)

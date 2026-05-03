from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Game, PlayerStats
from .services import (
    GameError,
    apply_move,
    arrange_checkers_for_victory_test,
    arrange_checkers_in_home,
    create_roll,
    finish_blocked_turn,
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
        self.assertContains(response, "👈")
        self.assertContains(response, 'data-debug-tools="1"')
        self.assertContains(response, "data-prepare-bear-off-url")

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

    @override_settings(BACKGAMMON_DEBUG_TOOLS=False)
    def test_debug_buttons_and_endpoint_are_disabled_when_setting_is_off(self) -> None:
        """Debug buttons are hidden and helper endpoints reject requests."""
        detail_response = self.client.get(
            reverse("backgammon:game_detail", kwargs={"pk": self.game.pk})
        )
        endpoint_response = self.client.post(
            reverse("backgammon:prepare_bear_off", kwargs={"pk": self.game.pk})
        )

        self.assertNotContains(detail_response, "В дом для теста")
        self.assertNotContains(detail_response, "Тест победы")
        self.assertContains(detail_response, 'data-debug-tools="0"')
        self.assertNotContains(detail_response, "data-prepare-bear-off-url")
        self.assertEqual(endpoint_response.status_code, 403)

import json
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.forms import modelform_factory
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .app_settings import (
    backgammon_animations_enabled,
    backgammon_checker_count_presets,
    backgammon_debug_tools,
    backgammon_dice_mode,
    backgammon_notification_display_ms,
    backgammon_poll_interval_ms,
    backgammon_quick_notifications_enabled,
)
from .admin import AppSettingAdminForm
from .models import (
    AppSetting,
    BackgammonPlayerPreference,
    Game,
    GameMove,
    GameNotification,
    PlayerStats,
    QuickNotificationPreset,
    initial_board_for_count,
)
from .realtime import game_group_name
from .routing import websocket_urlpatterns
from .services import (
    GameError,
    apply_move,
    arrange_blocking_event_test,
    arrange_checkers_for_victory_test,
    arrange_checkers_in_home,
    arrange_final_double_test,
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

    @override_settings(APP_VERSION="1.2.3")
    def test_base_template_shows_configured_app_version(self) -> None:
        """The global UI shows the application version when configured."""
        response = self.client.get(reverse("login"))

        self.assertContains(response, "Версия 1.2.3")

    @override_settings(APP_VERSION="develop")
    def test_base_template_shows_default_develop_version(self) -> None:
        """The global UI shows the development version fallback."""
        response = self.client.get(reverse("login"))

        self.assertContains(response, "Версия develop")


class AppSettingsTests(TestCase):
    """Coverage for DB-backed app settings with environment fallback."""

    @override_settings(BACKGAMMON_DEBUG_TOOLS=True)
    def test_boolean_setting_falls_back_when_row_is_missing(self) -> None:
        """Missing DB settings fall back to Django/env-derived settings."""
        AppSetting.objects.filter(key=AppSetting.Key.BACKGAMMON_DEBUG_TOOLS).delete()

        self.assertTrue(backgammon_debug_tools())

    @override_settings(BACKGAMMON_DEBUG_TOOLS=True)
    def test_boolean_setting_uses_enabled_database_value(self) -> None:
        """Enabled DB settings override environment-derived fallbacks."""
        AppSetting.objects.update_or_create(
            key=AppSetting.Key.BACKGAMMON_DEBUG_TOOLS,
            defaults={"value": "false", "is_enabled": True},
        )

        self.assertFalse(backgammon_debug_tools())

    @override_settings(BACKGAMMON_DEBUG_TOOLS=True)
    def test_disabled_setting_uses_fallback_value(self) -> None:
        """Disabled DB settings leave the environment fallback active."""
        AppSetting.objects.update_or_create(
            key=AppSetting.Key.BACKGAMMON_DEBUG_TOOLS,
            defaults={"value": "false", "is_enabled": False},
        )

        self.assertTrue(backgammon_debug_tools())

    @override_settings(BACKGAMMON_DICE_MODE="independent")
    def test_choice_setting_ignores_invalid_database_value(self) -> None:
        """Invalid DB choices do not replace the fallback setting."""
        AppSetting.objects.update_or_create(
            key=AppSetting.Key.BACKGAMMON_DICE_MODE,
            defaults={"value": "strange", "is_enabled": True},
        )

        self.assertEqual(backgammon_dice_mode(), "independent")

    @override_settings(BACKGAMMON_ANIMATIONS_ENABLED=False)
    def test_animation_setting_uses_database_value(self) -> None:
        """Animation settings are available through the DB-backed layer."""
        AppSetting.objects.update_or_create(
            key=AppSetting.Key.BACKGAMMON_ANIMATIONS_ENABLED,
            defaults={"value": "true", "is_enabled": True},
        )

        self.assertTrue(backgammon_animations_enabled())

    @override_settings(BACKGAMMON_POLL_INTERVAL_MS=1000)
    def test_poll_interval_setting_uses_minimum_value(self) -> None:
        """Poll interval values are clamped to the existing lower bound."""
        AppSetting.objects.update_or_create(
            key=AppSetting.Key.BACKGAMMON_POLL_INTERVAL_MS,
            defaults={"value": "50", "is_enabled": True},
        )

        self.assertEqual(backgammon_poll_interval_ms(), 250)

    @override_settings(BACKGAMMON_NOTIFICATION_DISPLAY_MS=4500)
    def test_notification_display_setting_uses_database_value(self) -> None:
        """Quick-notification display duration is DB-overridable."""
        AppSetting.objects.update_or_create(
            key=AppSetting.Key.BACKGAMMON_NOTIFICATION_DISPLAY_MS,
            defaults={"value": "1200", "is_enabled": True},
        )

        self.assertEqual(backgammon_notification_display_ms(), 1200)

    @override_settings(BACKGAMMON_NOTIFICATION_DISPLAY_MS=4500)
    def test_notification_display_setting_uses_minimum_value(self) -> None:
        """Quick-notification display duration has a safe lower bound."""
        AppSetting.objects.update_or_create(
            key=AppSetting.Key.BACKGAMMON_NOTIFICATION_DISPLAY_MS,
            defaults={"value": "50", "is_enabled": True},
        )

        self.assertEqual(backgammon_notification_display_ms(), 1000)

    @override_settings(BACKGAMMON_QUICK_NOTIFICATIONS_ENABLED=False)
    def test_quick_notifications_enabled_setting_uses_environment_value(self) -> None:
        """The quick-notification feature flag is environment-backed for now."""
        self.assertFalse(backgammon_quick_notifications_enabled())

    @override_settings(BACKGAMMON_QUICK_NOTIFICATIONS_ENABLED=True)
    def test_quick_notifications_enabled_setting_uses_database_value(self) -> None:
        """Enabled DB settings can override the quick-notification feature flag."""
        AppSetting.objects.update_or_create(
            key=AppSetting.Key.BACKGAMMON_QUICK_NOTIFICATIONS_ENABLED,
            defaults={"value": "false", "is_enabled": True},
        )

        self.assertFalse(backgammon_quick_notifications_enabled())

    @override_settings(BACKGAMMON_QUICK_NOTIFICATIONS_ENABLED=True)
    def test_disabled_quick_notifications_setting_uses_fallback_value(self) -> None:
        """Disabled quick-notification rows leave the environment fallback active."""
        AppSetting.objects.update_or_create(
            key=AppSetting.Key.BACKGAMMON_QUICK_NOTIFICATIONS_ENABLED,
            defaults={"value": "false", "is_enabled": False},
        )

        self.assertTrue(backgammon_quick_notifications_enabled())

    @override_settings(BACKGAMMON_CHECKER_COUNT_PRESETS=["3", "7"])
    def test_checker_count_presets_include_standard_default(self) -> None:
        """Checker-count presets come from settings and always include 15."""
        AppSetting.objects.filter(
            key=AppSetting.Key.BACKGAMMON_CHECKER_COUNT_PRESETS
        ).delete()

        self.assertEqual(backgammon_checker_count_presets(), [3, 7, 15])

    @override_settings(BACKGAMMON_CHECKER_COUNT_PRESETS=["3"])
    def test_checker_count_presets_use_database_value(self) -> None:
        """Enabled DB settings override the checker-count preset list."""
        AppSetting.objects.update_or_create(
            key=AppSetting.Key.BACKGAMMON_CHECKER_COUNT_PRESETS,
            defaults={"value": "5, 10, 20, 21, nope", "is_enabled": True},
        )

        self.assertEqual(backgammon_checker_count_presets(), [5, 10, 20, 15])

    def test_admin_form_shows_raw_setting_keys(self) -> None:
        """Admin key choices use the exact runtime setting names."""
        choices = dict(AppSettingAdminForm().fields["key"].choices)

        self.assertEqual(
            choices[AppSetting.Key.BACKGAMMON_DEBUG_TOOLS],
            AppSetting.Key.BACKGAMMON_DEBUG_TOOLS,
        )
        self.assertEqual(
            choices[AppSetting.Key.BACKGAMMON_DICE_MODE],
            AppSetting.Key.BACKGAMMON_DICE_MODE,
        )

    def test_admin_form_validates_boolean_values(self) -> None:
        """Boolean runtime settings reject values outside the accepted aliases."""
        form = AppSettingAdminForm(
            data={
                "key": AppSetting.Key.BACKGAMMON_DEBUG_TOOLS,
                "value": "maybe",
                "is_enabled": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("value", form.errors)

    def test_admin_form_validates_dice_mode_values(self) -> None:
        """Dice mode runtime settings allow only known generation modes."""
        form = AppSettingAdminForm(
            data={
                "key": AppSetting.Key.BACKGAMMON_DICE_MODE,
                "value": "weighted",
                "is_enabled": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("value", form.errors)

    def test_admin_form_validates_poll_interval_values(self) -> None:
        """Polling interval runtime settings must be valid milliseconds."""
        form = AppSettingAdminForm(
            data={
                "key": AppSetting.Key.BACKGAMMON_POLL_INTERVAL_MS,
                "value": "249",
                "is_enabled": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("value", form.errors)

    def test_admin_form_validates_notification_display_values(self) -> None:
        """Notification display duration must be valid milliseconds."""
        form = AppSettingAdminForm(
            data={
                "key": AppSetting.Key.BACKGAMMON_NOTIFICATION_DISPLAY_MS,
                "value": "999",
                "is_enabled": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("value", form.errors)

    def test_admin_form_validates_quick_notification_enabled_values(self) -> None:
        """Quick-notification feature flag accepts only boolean aliases."""
        form = AppSettingAdminForm(
            data={
                "key": AppSetting.Key.BACKGAMMON_QUICK_NOTIFICATIONS_ENABLED,
                "value": "sometimes",
                "is_enabled": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("value", form.errors)

    def test_admin_form_validates_checker_count_presets(self) -> None:
        """Checker-count presets must include usable integer choices."""
        form = AppSettingAdminForm(
            data={
                "key": AppSetting.Key.BACKGAMMON_CHECKER_COUNT_PRESETS,
                "value": "21, nope",
                "is_enabled": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("value", form.errors)

    def test_admin_form_accepts_and_normalizes_valid_values(self) -> None:
        """Valid runtime setting values are cleaned before saving."""
        setting = AppSetting.objects.get(key=AppSetting.Key.BACKGAMMON_DEBUG_TOOLS)
        form = AppSettingAdminForm(
            instance=setting,
            data={
                "key": AppSetting.Key.BACKGAMMON_DEBUG_TOOLS,
                "value": "YES",
                "is_enabled": "on",
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["value"], "yes")

    def test_admin_form_normalizes_checker_count_presets(self) -> None:
        """Valid checker-count presets are deduplicated and keep the default."""
        setting = AppSetting.objects.get(
            key=AppSetting.Key.BACKGAMMON_CHECKER_COUNT_PRESETS
        )
        form = AppSettingAdminForm(
            instance=setting,
            data={
                "key": AppSetting.Key.BACKGAMMON_CHECKER_COUNT_PRESETS,
                "value": "5,20,5",
                "is_enabled": "on",
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["value"], "5,20,15")


@override_settings(
    CHANNEL_LAYERS={
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }
)
class GameStateWebSocketTests(TransactionTestCase):
    """Coverage for realtime game state delivery."""

    def setUp(self) -> None:
        """Create users and one active game for WebSocket tests."""
        User = get_user_model()
        self.white = User.objects.create_user(username="white", password="pass")
        self.black = User.objects.create_user(username="black", password="pass")
        self.other = User.objects.create_user(username="other", password="pass")
        self.game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            status=Game.Status.ACTIVE,
        )
        self.application = URLRouter(websocket_urlpatterns)

    def communicator(
        self, user: object, game: Game | None = None
    ) -> WebsocketCommunicator:
        """Build a communicator with a pre-authenticated scope user."""
        game = game or self.game
        communicator = WebsocketCommunicator(
            self.application,
            f"/ws/games/{game.pk}/",
        )
        communicator.scope["user"] = user
        return communicator

    def test_participant_receives_initial_viewer_specific_state(self) -> None:
        """A seated player receives the current game state immediately."""
        async_to_sync(self.check_participant_receives_initial_viewer_specific_state)()

    async def check_participant_receives_initial_viewer_specific_state(self) -> None:
        """Connect as a participant and assert the initial state payload."""
        communicator = self.communicator(self.white)
        connected, _ = await communicator.connect()

        self.assertTrue(connected)
        message = await communicator.receive_json_from(timeout=5)
        self.assertEqual(message["type"], "game_state")
        self.assertEqual(message["game"]["id"], self.game.pk)
        self.assertEqual(message["game"]["viewer_color"], Game.Color.WHITE)
        self.assertIn("updated_at", message["game"])

        await communicator.disconnect()

    def test_rejects_anonymous_user(self) -> None:
        """Anonymous users cannot subscribe to a private board."""
        async_to_sync(self.check_rejects_anonymous_user)()

    async def check_rejects_anonymous_user(self) -> None:
        """Connect without authentication and assert the socket is rejected."""
        communicator = self.communicator(AnonymousUser())
        connected, _ = await communicator.connect()

        self.assertFalse(connected)

    def test_rejects_non_participant_for_active_game(self) -> None:
        """Active games are visible only to seated players."""
        async_to_sync(self.check_rejects_non_participant_for_active_game)()

    async def check_rejects_non_participant_for_active_game(self) -> None:
        """Connect as an unrelated user and assert the socket is rejected."""
        communicator = self.communicator(self.other)
        connected, _ = await communicator.connect()

        self.assertFalse(connected)

    def test_heartbeat_ping_receives_pong(self) -> None:
        """The browser heartbeat gets an application-level pong."""
        async_to_sync(self.check_heartbeat_ping_receives_pong)()

    async def check_heartbeat_ping_receives_pong(self) -> None:
        """Send a heartbeat ping and assert the consumer replies with pong."""
        communicator = self.communicator(self.black)
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from(timeout=5)

        await communicator.send_json_to({"type": "ping"})
        message = await communicator.receive_json_from(timeout=5)

        self.assertEqual(message, {"type": "pong"})
        await communicator.disconnect()

    def test_group_update_pushes_fresh_state(self) -> None:
        """A game update event asks the consumer to send fresh state."""
        async_to_sync(self.check_group_update_pushes_fresh_state)()

    async def check_group_update_pushes_fresh_state(self) -> None:
        """Publish a group update and assert a fresh game state is sent."""
        communicator = self.communicator(self.white)
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from(timeout=5)

        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            game_group_name(self.game.pk),
            {"type": "game.updated"},
        )
        message = await communicator.receive_json_from(timeout=5)

        self.assertEqual(message["type"], "game_state")
        self.assertEqual(message["game"]["id"], self.game.pk)
        await communicator.disconnect()

    def test_waiting_spectator_closes_after_game_becomes_private(self) -> None:
        """A waiting-game spectator stops receiving state once play starts."""
        async_to_sync(self.check_waiting_spectator_closes_after_game_becomes_private)()

    async def check_waiting_spectator_closes_after_game_becomes_private(self) -> None:
        """Assert a waiting-game spectator is closed after the game starts."""
        waiting_game = await Game.objects.acreate(
            white_player=self.white,
            status=Game.Status.WAITING,
        )
        communicator = self.communicator(self.other, waiting_game)
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        initial = await communicator.receive_json_from(timeout=5)
        self.assertIsNone(initial["game"]["viewer_color"])

        waiting_game.black_player = self.black
        waiting_game.current_player = self.white
        waiting_game.status = Game.Status.ACTIVE
        await waiting_game.asave(
            update_fields=["black_player", "current_player", "status", "updated_at"]
        )
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            game_group_name(waiting_game.pk),
            {"type": "game.updated"},
        )
        message = await communicator.receive_output(timeout=5)

        self.assertEqual(message["type"], "websocket.close")


class GameNotificationTests(TestCase):
    """Coverage for predefined in-game opponent notifications."""

    def setUp(self) -> None:
        """Create users and an active game."""
        User = get_user_model()
        self.white = User.objects.create_user(username="white", password="pass")
        self.black = User.objects.create_user(username="black", password="pass")
        self.other = User.objects.create_user(username="other", password="pass")
        self.game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            status=Game.Status.ACTIVE,
        )
        presets = (
            ("🎉", "Congratulations! 🎉", 10),
            ("🙂", "No connection! Call me back 🙂", 20),
            ("🎲", "Have a good game! 🎲", 30),
            ("📝", "And then they’ll write: it was a duplicate 📝", 40),
            ("🤔", "Interesting move 🤔", 50),
        )
        for emoji, text, sort_order in presets:
            QuickNotificationPreset.objects.update_or_create(
                text=text,
                defaults={
                    "emoji": emoji,
                    "sort_order": sort_order,
                },
            )

    def test_send_notification_persists_predefined_text_for_opponent(self) -> None:
        """A participant can send an allowed quick notification to the opponent."""
        self.client.force_login(self.white)

        response = self.client.post(
            reverse("backgammon:send_notification", args=[self.game.pk]),
            data=json.dumps({"text": "Congratulations! 🎉"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        notification = GameNotification.objects.get()
        self.assertEqual(notification.game, self.game)
        self.assertEqual(notification.sender, self.white)
        self.assertEqual(notification.recipient, self.black)
        self.assertEqual(notification.text, "Congratulations! 🎉")

    def test_new_notification_texts_can_be_sent(self) -> None:
        """All notification phrases added for compact controls are allowed."""
        self.client.force_login(self.white)
        texts = (
            "Have a good game! 🎲",
            "And then they’ll write: it was a duplicate 📝",
            "Interesting move 🤔",
        )

        for text in texts:
            with self.subTest(text=text):
                response = self.client.post(
                    reverse("backgammon:send_notification", args=[self.game.pk]),
                    data=json.dumps({"text": text}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 200)

        self.assertEqual(
            list(
                GameNotification.objects.order_by("pk").values_list(
                    "text",
                    flat=True,
                )
            ),
            list(texts),
        )

    def test_notification_controls_render_emoji_with_full_text_tooltips(self) -> None:
        """Compact controls keep each full phrase available before sending."""
        self.client.force_login(self.white)

        response = self.client.get(
            reverse("backgammon:game_detail", args=[self.game.pk])
        )

        self.assertContains(response, 'aria-describedby="quick-notification-tooltip-1"')
        self.assertContains(response, 'role="tooltip">Have a good game! 🎲</span>')
        self.assertContains(
            response,
            'role="tooltip">And then they’ll write: it was a duplicate 📝</span>',
        )
        self.assertContains(response, 'role="tooltip">Interesting move 🤔</span>')
        self.assertContains(response, ">🎲<span")
        self.assertContains(response, ">📝<span")
        self.assertContains(response, ">🤔<span")

    def test_notification_controls_follow_admin_sort_order(self) -> None:
        """The configured sort order controls button placement."""
        QuickNotificationPreset.objects.filter(text="Interesting move 🤔").update(
            sort_order=1
        )
        self.client.force_login(self.white)

        response = self.client.get(
            reverse("backgammon:game_detail", args=[self.game.pk])
        )
        content = response.content.decode()

        self.assertLess(
            content.index('role="tooltip">Interesting move 🤔</span>'),
            content.index('role="tooltip">Congratulations! 🎉</span>'),
        )

    def test_deleted_notification_preset_cannot_be_sent(self) -> None:
        """Removing an admin preset immediately removes it from the allowlist."""
        QuickNotificationPreset.objects.filter(text="Interesting move 🤔").delete()
        self.client.force_login(self.white)

        response = self.client.post(
            reverse("backgammon:send_notification", args=[self.game.pk]),
            data=json.dumps({"text": "Interesting move 🤔"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(GameNotification.objects.exists())

    def test_send_notification_rejects_free_form_text(self) -> None:
        """Free-form texts are rejected so the feature does not become chat."""
        self.client.force_login(self.white)

        response = self.client.post(
            reverse("backgammon:send_notification", args=[self.game.pk]),
            data=json.dumps({"text": "hello from chat"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(GameNotification.objects.exists())

    def test_non_participant_cannot_send_notification(self) -> None:
        """Only seated players can send quick notifications."""
        self.client.force_login(self.other)

        response = self.client.post(
            reverse("backgammon:send_notification", args=[self.game.pk]),
            data=json.dumps({"text": "Congratulations! 🎉"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(GameNotification.objects.exists())

    def test_recipient_payload_includes_recent_notification(self) -> None:
        """Serialized state exposes recent notifications to the recipient only."""
        GameNotification.objects.create(
            game=self.game,
            sender=self.white,
            recipient=self.black,
            text="Congratulations! 🎉",
        )

        sender_payload = serialize_game(self.game, self.white)
        recipient_payload = serialize_game(self.game, self.black)

        self.assertEqual(sender_payload["quick_notifications"], [])
        self.assertEqual(len(recipient_payload["quick_notifications"]), 1)
        self.assertEqual(
            recipient_payload["quick_notifications"][0]["text"],
            "Congratulations! 🎉",
        )

    @override_settings(BACKGAMMON_QUICK_NOTIFICATIONS_ENABLED=False)
    def test_disabled_notifications_are_not_serialized(self) -> None:
        """The env feature flag hides notification state from the browser."""
        GameNotification.objects.create(
            game=self.game,
            sender=self.white,
            recipient=self.black,
            text="Congratulations! 🎉",
        )

        payload = serialize_game(self.game, self.black)

        self.assertFalse(payload["can_send_quick_notifications"])
        self.assertEqual(payload["quick_notifications"], [])

    @override_settings(BACKGAMMON_QUICK_NOTIFICATIONS_ENABLED=False)
    def test_disabled_notifications_reject_send_endpoint(self) -> None:
        """The env feature flag prevents new notifications from being sent."""
        self.client.force_login(self.white)

        response = self.client.post(
            reverse("backgammon:send_notification", args=[self.game.pk]),
            data=json.dumps({"text": "Congratulations! 🎉"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(GameNotification.objects.exists())

    @patch("backgammon.views.notify_game_updated")
    def test_send_notification_publishes_realtime_update_after_commit(
        self,
        notify_game_updated_mock,
    ) -> None:
        """Sending a notification queues the same realtime update flow as moves."""
        self.client.force_login(self.white)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("backgammon:send_notification", args=[self.game.pk]),
                data=json.dumps({"text": "No connection! Call me back 🙂"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        notify_game_updated_mock.assert_called_once_with(self.game.pk)


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

    def test_game_admin_form_accepts_empty_dice_lists(self) -> None:
        """Admin-style game forms allow the empty-list state before/after a roll."""
        game = Game.objects.create(white_player=self.white)
        form_class = modelform_factory(
            Game,
            fields=[
                "white_player",
                "black_player",
                "current_player",
                "winner",
                "status",
                "victory_type",
                "board",
                "borne_off",
                "dice",
                "remaining_moves",
                "has_rolled",
                "head_moves_this_turn",
                "turn_number",
            ],
        )
        form = form_class(
            instance=game,
            data={
                "white_player": str(self.white.pk),
                "black_player": "",
                "current_player": "",
                "winner": "",
                "status": Game.Status.WAITING,
                "victory_type": "",
                "board": json.dumps(game.board),
                "borne_off": json.dumps(game.borne_off),
                "dice": "[]",
                "remaining_moves": "[]",
                "has_rolled": "",
                "head_moves_this_turn": "0",
                "turn_number": "1",
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["dice"], [])
        self.assertEqual(form.cleaned_data["remaining_moves"], [])

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

    def test_bear_off_uses_game_checker_count_for_victory(self) -> None:
        """Short games finish when their configured checker count is borne off."""
        game = self.active_game()
        game.checker_count = 5
        game.board = [None for _ in range(24)]
        game.board[23] = {"color": Game.Color.WHITE, "count": 1}
        game.borne_off = {Game.Color.WHITE: 4, Game.Color.BLACK: 0}
        game.dice = [1]
        game.remaining_moves = [1]
        game.save()

        apply_move(game, self.white, 23, 1)
        game.refresh_from_db()

        self.assertEqual(game.status, Game.Status.FINISHED)
        self.assertEqual(game.borne_off[Game.Color.WHITE], 5)

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
        self.white.first_name = "White"
        self.white.last_name = "Player"
        self.white.save(update_fields=["first_name", "last_name"])
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
        GameMove.objects.create(
            game=game,
            player=self.white,
            action=GameMove.Action.MOVE,
            dice=[6, 6, 6, 6],
        )
        GameMove.objects.create(
            game=game,
            player=self.white,
            action=GameMove.Action.MOVE,
            dice=[6, 6, 6],
        )

        payload = serialize_game(game, self.white)

        self.assertEqual(payload["started_at"], "2026-05-03T10:05:00+00:00")
        self.assertEqual(payload["finished_at"], "2026-05-03T11:23:00+00:00")
        self.assertEqual(payload["double_rolls"], {"white": 1, "black": 1})
        self.assertEqual(
            payload["dice_statistics"],
            {
                "white": {
                    "total_points": 15,
                    "double_rolls": 1,
                    "double_moves_used": 2,
                    "double_moves_available": 4,
                },
                "black": {
                    "total_points": 6,
                    "double_rolls": 1,
                    "double_moves_used": 0,
                    "double_moves_available": 4,
                },
            },
        )
        self.assertEqual(payload["white_player"]["display_name"], "White Player")
        self.assertEqual(payload["black_player"]["display_name"], "black")

    def test_serialized_game_counts_skipped_turns_without_moves(self) -> None:
        """Skipped turns are rolls followed by another roll without a checker move."""
        game = self.active_game()
        for player, action, dice in [
            (self.white, GameMove.Action.ROLL, [3, 3]),
            (self.black, GameMove.Action.ROLL, [1, 2]),
            (self.white, GameMove.Action.ROLL, [1, 2]),
            (self.black, GameMove.Action.ROLL, [1, 2]),
            (self.black, GameMove.Action.MOVE, []),
            (self.white, GameMove.Action.ROLL, [1, 2]),
            (self.black, GameMove.Action.FINISH, []),
        ]:
            GameMove.objects.create(
                game=game,
                player=player,
                action=action,
                dice=dice,
            )

        payload = serialize_game(game, self.white)

        self.assertEqual(payload["skipped_turns"], {"white": 2, "black": 1})
        self.assertEqual(payload["skipped_moves"], {"white": 6, "black": 2})
        self.assertEqual(payload["skipped_points"], {"white": 9, "black": 3})

    def test_final_double_roll_is_excluded_from_dice_statistics(self) -> None:
        """A winning double does not count as skipped dice moves or points."""
        game = self.active_game()
        arrange_final_double_test(game, self.white)

        apply_move(game, self.white, 22, 4)
        game.refresh_from_db()

        payload = serialize_game(game, self.white)

        self.assertEqual(game.status, Game.Status.FINISHED)
        self.assertEqual(
            payload["dice_statistics"]["white"],
            {
                "total_points": 0,
                "double_rolls": 0,
                "double_moves_used": 0,
                "double_moves_available": 0,
            },
        )
        self.assertEqual(payload["skipped_turns"], {"white": 0, "black": 0})
        self.assertEqual(payload["skipped_moves"], {"white": 0, "black": 0})
        self.assertEqual(payload["skipped_points"], {"white": 0, "black": 0})

    def test_finished_stats_show_opponent_skipped_turns_not_borne_off_totals(
        self,
    ) -> None:
        """The finished-game UI keeps borne-off totals on the board only."""
        script = Path(__file__).with_name("static") / "backgammon" / "game.js"
        source = script.read_text()

        self.assertIn("Пропуск (${playerName(game.white_player)})", source)
        self.assertIn("Пропуск (${playerName(game.black_player)})", source)
        self.assertIn("skippedTurns.black", source)
        self.assertIn("skippedTurns.white", source)
        self.assertIn("skippedPoints.black", source)
        self.assertIn("skippedPoints.white", source)
        self.assertNotIn("📤 Выведено", source)

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
        self.viewer = User.objects.create_user(
            username="viewer",
            password="pass",
            first_name="Vera",
            last_name="Viewer",
        )
        self.opponent = User.objects.create_user(username="opponent", password="pass")
        self.client.force_login(self.viewer)

    def test_lobby_renders_game_setup_modal_with_user_defaults(self) -> None:
        """The new-game modal is prefilled from user and runtime settings."""
        BackgammonPlayerPreference.objects.create(
            user=self.viewer,
            default_checker_color=Game.Color.BLACK,
        )
        AppSetting.objects.update_or_create(
            key=AppSetting.Key.BACKGAMMON_CHECKER_COUNT_PRESETS,
            defaults={"value": "5,10", "is_enabled": True},
        )

        response = self.client.get(reverse("backgammon:game_list"))

        self.assertContains(response, 'id="gameSetupModal"')
        self.assertContains(response, 'value="black" checked')
        self.assertContains(response, 'name="checker_count"')
        self.assertContains(response, 'value="5"')
        self.assertContains(response, 'value="10"')
        self.assertContains(response, 'value="15" checked')

    def test_create_game_uses_selected_color_and_checker_count(self) -> None:
        """Posting setup choices creates a waiting game with the selected setup."""
        AppSetting.objects.update_or_create(
            key=AppSetting.Key.BACKGAMMON_CHECKER_COUNT_PRESETS,
            defaults={"value": "5,15", "is_enabled": True},
        )

        response = self.client.post(
            reverse("backgammon:create_game"),
            {"color": Game.Color.BLACK, "checker_count": "5"},
        )

        game = Game.objects.get()
        self.assertRedirects(
            response, reverse("backgammon:game_detail", args=[game.pk])
        )
        self.assertIsNone(game.white_player)
        self.assertEqual(game.black_player, self.viewer)
        self.assertEqual(game.checker_count, 5)
        self.assertEqual(game.board, initial_board_for_count(5))

    @patch("backgammon.views.roll_die", side_effect=[6, 1])
    def test_join_game_fills_open_white_seat_for_black_creator(
        self, mocked_roll
    ) -> None:
        """A second player joins as white when the creator chose black."""
        game = Game.objects.create(
            black_player=self.viewer,
            checker_count=5,
            board=initial_board_for_count(5),
        )
        self.client.force_login(self.opponent)

        response = self.client.post(reverse("backgammon:join_game", args=[game.pk]))
        game.refresh_from_db()

        self.assertRedirects(
            response, reverse("backgammon:game_detail", args=[game.pk])
        )
        self.assertEqual(game.white_player, self.opponent)
        self.assertEqual(game.black_player, self.viewer)
        self.assertEqual(game.current_player, self.opponent)
        self.assertEqual(game.status, Game.Status.ACTIVE)
        mocked_roll.assert_called()

    def test_create_game_rejects_unconfigured_checker_count(self) -> None:
        """Game creation accepts only the configured checker-count presets."""
        AppSetting.objects.update_or_create(
            key=AppSetting.Key.BACKGAMMON_CHECKER_COUNT_PRESETS,
            defaults={"value": "5,15", "is_enabled": True},
        )

        response = self.client.post(
            reverse("backgammon:create_game"),
            {"color": Game.Color.WHITE, "checker_count": "10"},
        )

        self.assertRedirects(response, reverse("backgammon:game_list"))
        self.assertFalse(Game.objects.exists())

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
        self.assertContains(response, "🎲 15 фишек")
        self.assertNotContains(response, "дубли:")
        self.assertContains(response, "Vera Viewer ⇆ opponent")
        self.assertContains(response, "Vera Viewer ⇆ opponent 🌚")
        self.assertNotContains(response, "viewer ⇆ opponent")
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

    def test_active_game_detail_includes_current_duration(self) -> None:
        """The board includes a live duration slot for an active game."""
        game = Game.objects.create(
            white_player=self.viewer,
            black_player=self.opponent,
            current_player=self.viewer,
            status=Game.Status.ACTIVE,
        )

        response = self.client.get(reverse("backgammon:game_detail", args=[game.pk]))

        self.assertContains(response, 'id="game-duration-line"')
        self.assertContains(response, 'id="game-duration"')
        self.assertContains(response, "⌛ <span")
        self.assertNotContains(response, "⌛ Время:")


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
        self.assertContains(response, "Тест финального дубля")
        self.assertContains(response, "Тест головы 5/5")
        self.assertContains(response, "Тест блока 6")
        self.assertContains(response, "👈")
        self.assertContains(response, 'data-debug-tools="1"')
        self.assertContains(response, "data-prepare-bear-off-url")
        self.assertContains(response, "data-prepare-final-double-url")
        self.assertContains(response, "data-prepare-extra-head-move-url")
        self.assertContains(response, "data-prepare-blocking-event-url")

    @override_settings(BACKGAMMON_DEBUG_TOOLS=True)
    def test_final_double_debug_helper_prepares_winning_roll(self) -> None:
        """The final-double helper leaves one checker for a 4/4 bear-off."""
        response = self.client.post(
            reverse("backgammon:prepare_final_double", kwargs={"pk": self.game.pk})
        )
        self.game.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(self.game.dice, [4, 4])
        self.assertEqual(self.game.remaining_moves, [4, 4, 4, 4])
        self.assertEqual(self.game.borne_off[Game.Color.WHITE], 14)
        self.assertEqual(self.game.board[22], {"color": Game.Color.WHITE, "count": 1})

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

    def test_game_detail_renders_borne_off_checker_counters(self) -> None:
        """Borne-off totals are rendered on checker-shaped board controls."""
        response = self.client.get(
            reverse("backgammon:game_detail", kwargs={"pk": self.game.pk})
        )

        self.assertContains(response, 'id="off-board" class="off-board"')
        self.assertContains(
            response, 'id="black-off-checker" class="checker black off-board-checker"'
        )
        self.assertContains(
            response, 'id="white-off-checker" class="checker white off-board-checker"'
        )

    @override_settings(BACKGAMMON_POLL_INTERVAL_MS=750)
    def test_poll_interval_setting_renders_for_frontend(self) -> None:
        """The frontend receives the configured state polling interval."""
        response = self.client.get(
            reverse("backgammon:game_detail", kwargs={"pk": self.game.pk})
        )

        self.assertContains(response, 'data-poll-interval-ms="750"')

    def test_websocket_url_renders_for_frontend(self) -> None:
        """The frontend receives the game WebSocket path."""
        response = self.client.get(
            reverse("backgammon:game_detail", kwargs={"pk": self.game.pk})
        )

        self.assertContains(response, f'data-state-ws-url="/ws/games/{self.game.pk}/"')

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
        self.assertNotContains(detail_response, "Тест финального дубля")
        self.assertNotContains(detail_response, "Тест головы 5/5")
        self.assertNotContains(detail_response, "Тест блока 6")
        self.assertContains(detail_response, 'data-debug-tools="0"')
        self.assertNotContains(detail_response, "data-prepare-bear-off-url")
        self.assertNotContains(detail_response, "data-prepare-final-double-url")
        self.assertNotContains(detail_response, "data-prepare-extra-head-move-url")
        self.assertNotContains(detail_response, "data-prepare-blocking-event-url")
        self.assertEqual(endpoint_response.status_code, 403)
        self.assertEqual(extra_head_response.status_code, 403)
        self.assertEqual(blocking_event_response.status_code, 403)

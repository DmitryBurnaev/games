import os
import runpy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from backgammon.views import game_duration_label
from games import settings as project_settings


class DurationLabelTests(SimpleTestCase):
    def test_zero_components_are_omitted(self) -> None:
        cases = (
            (timedelta(), "0 мин"),
            (timedelta(minutes=25), "25 мин"),
            (timedelta(days=1, minutes=5), "1 д 5 мин"),
            (timedelta(hours=2), "2 часа"),
        )
        for duration, label in cases:
            with self.subTest(duration=duration):
                self.assertEqual(game_duration_label(duration), label)


class CookieSecurityTests(SimpleTestCase):
    def test_secure_cookie_settings_follow_debug_and_environment(self) -> None:
        for debug, overrides, expected in (
            ("true", {}, (False, False)),
            ("false", {}, (True, True)),
            (
                "false",
                {
                    "DJANGO_SESSION_COOKIE_SECURE": "false",
                    "DJANGO_CSRF_COOKIE_SECURE": "false",
                },
                (False, False),
            ),
            (
                "true",
                {
                    "DJANGO_SESSION_COOKIE_SECURE": "true",
                    "DJANGO_CSRF_COOKIE_SECURE": "true",
                },
                (True, True),
            ),
        ):
            with self.subTest(debug=debug, overrides=overrides):
                env = {
                    "DJANGO_DEBUG": debug,
                    "DJANGO_SECRET_KEY": "test-only-key",
                    **overrides,
                }
                with patch.dict(os.environ, env, clear=True):
                    config = runpy.run_path(project_settings.__file__)
                self.assertEqual(
                    (config["SESSION_COOKIE_SECURE"], config["CSRF_COOKIE_SECURE"]),
                    expected,
                )

    @override_settings(SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True)
    def test_login_page_sets_secure_csrf_cookie(self) -> None:
        response = self.client.get(reverse("login"), secure=True)
        self.assertTrue(response.cookies[settings.CSRF_COOKIE_NAME]["secure"])


class SessionInactivityTests(TestCase):
    @override_settings(SESSION_COOKIE_SECURE=True)
    def test_login_sets_secure_session_cookie(self) -> None:
        user = get_user_model().objects.create_user(
            username="secure-player", password="test-password"
        )
        response = self.client.post(
            reverse("login"),
            {"username": user.username, "password": "test-password"},
            secure=True,
        )
        self.assertTrue(response.cookies[settings.SESSION_COOKIE_NAME]["secure"])

    def test_login_activity_restart_expiry_and_logout(self) -> None:
        user = get_user_model().objects.create_user(
            username="player", password="test-password"
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch("django.utils.timezone.now", return_value=start):
            response = self.client.post(
                reverse("login"),
                {"username": user.username, "password": "test-password"},
            )
            self.assertEqual(response.status_code, 302)
            cookie = response.cookies[settings.SESSION_COOKIE_NAME]
            self.assertEqual(cookie["max-age"], 30 * 24 * 60 * 60)
            key = cookie.value
            self.assertEqual(
                Session.objects.get(session_key=key).expire_date,
                start + timedelta(days=30),
            )

        # A new client represents a browser restart with its persistent cookie restored;
        # the session is loaded anew from the database, as after an application restart.
        restarted = Client()
        restarted.cookies[settings.SESSION_COOKIE_NAME] = key
        active = start + timedelta(days=29)
        with patch("django.utils.timezone.now", return_value=active):
            response = restarted.get(reverse("backgammon:game_list"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.cookies[settings.SESSION_COOKIE_NAME]["max-age"],
                30 * 24 * 60 * 60,
            )
            self.assertEqual(
                Session.objects.get(session_key=key).expire_date,
                active + timedelta(days=30),
            )

        with patch(
            "django.utils.timezone.now", return_value=active + timedelta(days=30)
        ):
            self.assertEqual(
                restarted.get(reverse("backgammon:game_list")).status_code, 302
            )

        with patch(
            "django.utils.timezone.now", return_value=active + timedelta(days=31)
        ):
            restarted.post(
                reverse("login"),
                {"username": user.username, "password": "test-password"},
            )
            key = restarted.cookies[settings.SESSION_COOKIE_NAME].value
            restarted.post(reverse("logout"))
            self.assertFalse(Session.objects.filter(session_key=key).exists())
            self.assertEqual(
                restarted.get(reverse("backgammon:game_list")).status_code, 302
            )

    def test_password_change_revokes_existing_session(self) -> None:
        user = get_user_model().objects.create_user(
            username="player", password="test-password"
        )
        self.client.force_login(user)
        validate_password("new-password", user)
        user.set_password("new-password")
        user.save()
        self.assertEqual(
            self.client.get(reverse("backgammon:game_list")).status_code, 302
        )

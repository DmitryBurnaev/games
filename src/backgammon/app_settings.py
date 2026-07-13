from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.db.utils import DatabaseError, OperationalError, ProgrammingError

from .models import AppSetting
from games.settings import DEFAULT_CHECKER_COUNT

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
DICE_MODE_INDEPENDENT = "independent"
DICE_MODE_PLAYER_BAG = "player_bag"
DICE_MODES = {DICE_MODE_INDEPENDENT, DICE_MODE_PLAYER_BAG}
MIN_CHECKER_COUNT = 1
MAX_CHECKER_COUNT = 15


def active_setting_value(key: AppSetting.Key) -> str | None:
    """Return an enabled DB setting value, falling back when unavailable."""
    try:
        return (
            AppSetting.objects.filter(key=key, is_enabled=True)
            .values_list("value", flat=True)
            .first()
        )
    except DatabaseError, OperationalError, ProgrammingError:
        return None


def bool_setting(key: AppSetting.Key, fallback: Callable[[], bool]) -> bool:
    """Read a boolean setting from DB, falling back for missing/invalid values."""
    value = active_setting_value(key)
    if value is None:
        return fallback()
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return fallback()


def int_setting(
    key: AppSetting.Key,
    fallback: Callable[[], int],
    min_value: int | None = None,
) -> int:
    """Read an integer setting from DB, falling back for missing/invalid values."""
    value = active_setting_value(key)
    if value is None:
        return fallback()
    try:
        parsed = int(value)
    except ValueError:
        return fallback()
    if min_value is not None:
        return max(parsed, min_value)
    return parsed


def choice_setting(
    key: AppSetting.Key,
    fallback: Callable[[], str],
    choices: set[str],
) -> str:
    """Read a string setting constrained to known choices."""
    value = active_setting_value(key)
    if value is None:
        return fallback()
    normalized = value.strip()
    return normalized if normalized in choices else fallback()


def checker_count_presets_from_value(value: str) -> list[int]:
    """Parse configured checker-count presets while preserving their order."""
    presets: list[int] = []
    for item in value.split(","):
        try:
            count = int(item.strip())
        except ValueError:
            continue
        if count < MIN_CHECKER_COUNT or count > MAX_CHECKER_COUNT:
            continue
        if count not in presets:
            presets.append(count)
    if DEFAULT_CHECKER_COUNT not in presets:
        presets.append(DEFAULT_CHECKER_COUNT)
    return presets


def backgammon_debug_tools() -> bool:
    """Return whether debug helpers are enabled for backgammon."""
    return bool_setting(
        AppSetting.Key.BACKGAMMON_DEBUG_TOOLS,
        lambda: settings.BACKGAMMON_DEBUG_TOOLS,
    )


def backgammon_dice_mode() -> str:
    """Return the configured backgammon dice generation mode."""
    return choice_setting(
        AppSetting.Key.BACKGAMMON_DICE_MODE,
        lambda: settings.BACKGAMMON_DICE_MODE,
        DICE_MODES,
    )


def backgammon_animations_enabled() -> bool:
    """Return whether backgammon move animations are enabled."""
    return bool_setting(
        AppSetting.Key.BACKGAMMON_ANIMATIONS_ENABLED,
        lambda: settings.BACKGAMMON_ANIMATIONS_ENABLED,
    )


def backgammon_poll_interval_ms() -> int:
    """Return the browser poll interval for game state updates."""
    return int_setting(
        AppSetting.Key.BACKGAMMON_POLL_INTERVAL_MS,
        lambda: settings.BACKGAMMON_POLL_INTERVAL_MS,
        min_value=250,
    )


def backgammon_quick_notifications_enabled() -> bool:
    """Return whether quick opponent notifications are enabled."""
    return bool_setting(
        AppSetting.Key.BACKGAMMON_QUICK_NOTIFICATIONS_ENABLED,
        lambda: settings.BACKGAMMON_QUICK_NOTIFICATIONS_ENABLED,
    )


def backgammon_notification_display_ms() -> int:
    """Return how long quick notifications stay visible in the browser."""
    return int_setting(
        AppSetting.Key.BACKGAMMON_NOTIFICATION_DISPLAY_MS,
        lambda: settings.BACKGAMMON_NOTIFICATION_DISPLAY_MS,
        min_value=1000,
    )


def backgammon_checker_count_presets() -> list[int]:
    """Return the configured checker-count choices for new games."""
    value = active_setting_value(AppSetting.Key.BACKGAMMON_CHECKER_COUNT_PRESETS)
    if value is None:
        value = ",".join(
            str(item) for item in settings.BACKGAMMON_CHECKER_COUNT_PRESETS
        )
    return checker_count_presets_from_value(value) or [DEFAULT_CHECKER_COUNT]

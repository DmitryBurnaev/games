from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.db.utils import DatabaseError, OperationalError, ProgrammingError

from .models import AppSetting

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
DICE_MODE_INDEPENDENT = "independent"
DICE_MODE_PLAYER_BAG = "player_bag"
DICE_MODES = {DICE_MODE_INDEPENDENT, DICE_MODE_PLAYER_BAG}


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

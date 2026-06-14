from django import forms
from django.contrib import admin
from django.utils.safestring import mark_safe

from .app_settings import DICE_MODES, FALSE_VALUES, TRUE_VALUES
from .models import AppSetting, Game, GameMove, GameNotification, PlayerStats

BOOLEAN_SETTING_KEYS = {
    AppSetting.Key.BACKGAMMON_DEBUG_TOOLS,
    AppSetting.Key.BACKGAMMON_ANIMATIONS_ENABLED,
}
RAW_SETTING_KEY_CHOICES = [(key, key) for key in AppSetting.Key.values]

APP_SETTING_VALUE_HELP = mark_safe(
    "Allowed values by key:<br>"
    "BACKGAMMON_DEBUG_TOOLS: true, false, 1, 0, yes, no, on, off<br>"
    "BACKGAMMON_DICE_MODE: independent, player_bag<br>"
    "BACKGAMMON_ANIMATIONS_ENABLED: true, false, 1, 0, yes, no, on, off<br>"
    "BACKGAMMON_POLL_INTERVAL_MS: integer milliseconds, minimum effective value is 250<br>"
    "BACKGAMMON_NOTIFICATION_DISPLAY_MS: integer milliseconds, minimum effective value is 1000"
)


class AppSettingAdminForm(forms.ModelForm):
    """Admin form with constrained setting keys while keeping DB schema flexible."""

    key = forms.ChoiceField(choices=RAW_SETTING_KEY_CHOICES)
    value = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=APP_SETTING_VALUE_HELP,
    )

    class Meta:
        model = AppSetting
        fields = "__all__"

    def clean_value(self) -> str:
        """Validate setting values according to the selected key."""
        key = self.cleaned_data.get("key")
        value = self.cleaned_data.get("value", "").strip()

        if key in BOOLEAN_SETTING_KEYS:
            normalized = value.lower()
            if normalized not in TRUE_VALUES | FALSE_VALUES:
                raise forms.ValidationError(
                    "Use one of: true, false, 1, 0, yes, no, on, off."
                )
            return normalized

        if key == AppSetting.Key.BACKGAMMON_DICE_MODE:
            if value not in DICE_MODES:
                raise forms.ValidationError("Use one of: independent, player_bag.")
            return value

        if key in {
            AppSetting.Key.BACKGAMMON_POLL_INTERVAL_MS,
            AppSetting.Key.BACKGAMMON_NOTIFICATION_DISPLAY_MS,
        }:
            try:
                parsed = int(value)
            except ValueError:
                raise forms.ValidationError("Use an integer number of milliseconds.")
            min_value = (
                250 if key == AppSetting.Key.BACKGAMMON_POLL_INTERVAL_MS else 1000
            )
            if parsed < min_value:
                raise forms.ValidationError(
                    f"Use an integer value of {min_value} or greater."
                )
            return str(parsed)

        return value


@admin.register(AppSetting)
class AppSettingAdmin(admin.ModelAdmin):
    """Admin view for runtime settings with environment fallback."""

    form = AppSettingAdminForm
    list_display = ("key", "value", "is_enabled", "updated_at")
    list_filter = ("is_enabled", "created_at", "updated_at")
    search_fields = ("key", "value")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    """Admin view for inspecting active and completed games."""

    list_display = (
        "id",
        "white_player",
        "black_player",
        "status",
        "current_player",
        "winner",
        "victory_type",
        "started_at",
        "created_at",
    )
    list_filter = ("status", "victory_type", "started_at", "created_at")
    search_fields = (
        "white_player__username",
        "black_player__username",
        "winner__username",
    )
    readonly_fields = ("created_at", "updated_at", "started_at", "finished_at")


@admin.register(GameMove)
class GameMoveAdmin(admin.ModelAdmin):
    """Admin view for the immutable game-event history."""

    list_display = (
        "id",
        "game",
        "player",
        "action",
        "source_point",
        "target_point",
        "distance",
        "created_at",
    )
    list_filter = ("action", "created_at")
    search_fields = ("game__id", "player__username")


@admin.register(GameNotification)
class GameNotificationAdmin(admin.ModelAdmin):
    """Admin view for persisted quick notifications."""

    list_display = ("id", "game", "sender", "recipient", "text", "created_at")
    list_filter = ("created_at",)
    search_fields = (
        "game__id",
        "sender__username",
        "recipient__username",
        "text",
    )
    readonly_fields = ("created_at",)


@admin.register(PlayerStats)
class PlayerStatsAdmin(admin.ModelAdmin):
    """Admin view for aggregate player statistics."""

    list_display = (
        "user",
        "games_played",
        "wins",
        "losses",
        "oin_wins",
        "mars_wins",
        "updated_at",
    )
    search_fields = ("user__username",)

from django.contrib import admin

from .models import Game, GameMove, PlayerStats


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

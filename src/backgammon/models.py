from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from games.settings import DEFAULT_CHECKER_COUNT

BoardPoint = dict[str, Any] | None
Board = list[BoardPoint]
UserLike = Any


class AppSetting(models.Model):
    """Runtime-editable application setting with environment fallback."""

    class Key(models.TextChoices):
        BACKGAMMON_DEBUG_TOOLS = "BACKGAMMON_DEBUG_TOOLS", "Backgammon debug tools"
        BACKGAMMON_DICE_MODE = "BACKGAMMON_DICE_MODE", "Backgammon dice mode"
        BACKGAMMON_ANIMATIONS_ENABLED = (
            "BACKGAMMON_ANIMATIONS_ENABLED",
            "Backgammon animations enabled",
        )
        BACKGAMMON_POLL_INTERVAL_MS = (
            "BACKGAMMON_POLL_INTERVAL_MS",
            "Backgammon poll interval ms",
        )
        BACKGAMMON_QUICK_NOTIFICATIONS_ENABLED = (
            "BACKGAMMON_QUICK_NOTIFICATIONS_ENABLED",
            "Backgammon quick notifications enabled",
        )
        BACKGAMMON_NOTIFICATION_DISPLAY_MS = (
            "BACKGAMMON_NOTIFICATION_DISPLAY_MS",
            "Backgammon notification display ms",
        )
        BACKGAMMON_CHECKER_COUNT_PRESETS = (
            "BACKGAMMON_CHECKER_COUNT_PRESETS",
            "Backgammon checker count presets",
        )

    key = models.CharField(max_length=96, unique=True)
    value = models.TextField(blank=True)
    is_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self) -> str:
        state = "enabled" if self.is_enabled else "fallback"
        return f"{self.key} ({state})"

    def clean(self) -> None:
        """Validate keys in Python so new keys do not require schema changes."""
        super().clean()
        if self.key not in self.Key.values:
            raise ValidationError({"key": "Unknown application setting key."})


def initial_board_for_count(checker_count: int = DEFAULT_CHECKER_COUNT) -> Board:
    """Return the starting long-backgammon board layout."""
    board: Board = [None for _ in range(24)]
    board[0] = {"color": Game.Color.WHITE, "count": checker_count}
    board[12] = {"color": Game.Color.BLACK, "count": checker_count}
    return board


def initial_board() -> Board:
    """Return the standard 15-checker starting board."""
    return initial_board_for_count()


class Game(models.Model):
    """A single online long-backgammon game and its current board state."""

    class Color(models.TextChoices):
        WHITE = "white", "White"
        BLACK = "black", "Black"

    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting for opponent"
        ACTIVE = "active", "Active"
        FINISHED = "finished", "Finished"

    class VictoryType(models.TextChoices):
        OIN = "oin", "Oin"
        MARS = "mars", "Mars"

    white_player = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="white_backgammon_games",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    black_player = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="black_backgammon_games",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    planned_opponent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="planned_backgammon_games",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    current_player = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="current_backgammon_games",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    winner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="won_backgammon_games",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    status = models.CharField(max_length=16, choices=Status, default=Status.WAITING)
    victory_type = models.CharField(max_length=8, choices=VictoryType, blank=True)
    checker_count = models.PositiveSmallIntegerField(default=DEFAULT_CHECKER_COUNT)
    party_number = models.PositiveIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1)],
    )
    board = models.JSONField(default=initial_board)
    borne_off = models.JSONField(default=dict)
    dice = models.JSONField(default=list, blank=True)
    remaining_moves = models.JSONField(default=list, blank=True)
    has_rolled = models.BooleanField(default=False)
    head_moves_this_turn = models.PositiveSmallIntegerField(default=0)
    turn_number = models.PositiveIntegerField(default=1)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        white = self.white_player.username if self.white_player else "waiting"
        black = self.black_player.username if self.black_player else "waiting"
        return f"Game #{self.pk}: {white} vs {black}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Ensure borne-off counters are initialized before saving."""
        if not self.borne_off:
            self.borne_off = {self.Color.WHITE: 0, self.Color.BLACK: 0}
        super().save(*args, **kwargs)

    @property
    def players(self) -> list[UserLike]:
        """Return the users currently seated in the game."""
        return [player for player in [self.white_player, self.black_player] if player]

    def color_for(self, user: UserLike) -> Color | None:
        """Return the checker color assigned to a user in this game."""
        if user == self.white_player:
            return self.Color.WHITE
        if user == self.black_player:
            return self.Color.BLACK
        return None

    def opponent_for(self, user: UserLike) -> UserLike | None:
        """Return the opponent user for a seated player."""
        if user == self.white_player:
            return self.black_player
        if user == self.black_player:
            return self.white_player
        return None

    def mark_finished(self, winner: UserLike, victory_type: VictoryType) -> None:
        """Mark the game as finished with a winner and victory type."""
        self.status = self.Status.FINISHED
        self.winner = winner
        self.victory_type = victory_type
        self.finished_at = timezone.now()


class GameMove(models.Model):
    """An immutable history event for rolls, checker moves, and game finish."""

    class Action(models.TextChoices):
        JOIN = "join", "Join"
        ROLL = "roll", "Roll"
        MOVE = "move", "Move"
        BEAR_OFF = "bear_off", "Bear off"
        FINISH = "finish", "Finish"

    game = models.ForeignKey(Game, related_name="moves", on_delete=models.CASCADE)
    player = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    action = models.CharField(max_length=16, choices=Action)
    dice = models.JSONField(default=list)
    source_point = models.PositiveSmallIntegerField(blank=True, null=True)
    target_point = models.PositiveSmallIntegerField(blank=True, null=True)
    distance = models.PositiveSmallIntegerField(blank=True, null=True)
    board = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.get_action_display()} in game #{self.game_id}"


class BackgammonPlayerPreference(models.Model):
    """User-level backgammon preferences managed through Django admin."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name="backgammon_preferences",
        on_delete=models.CASCADE,
    )
    default_checker_color = models.CharField(
        max_length=8,
        choices=Game.Color,
        default=Game.Color.WHITE,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self) -> str:
        return f"Backgammon preferences for {self.user}"


class QuickNotificationPreset(models.Model):
    """An admin-configurable quick notification available during a game."""

    emoji = models.CharField(max_length=16)
    text = models.CharField(max_length=160, unique=True)
    sort_order = models.PositiveIntegerField(default=0, db_index=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.emoji} {self.text}"


class GameNotification(models.Model):
    """A persisted predefined quick notification sent between game opponents."""

    game = models.ForeignKey(
        Game,
        related_name="notifications",
        on_delete=models.CASCADE,
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sent_backgammon_notifications",
        on_delete=models.CASCADE,
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="received_backgammon_notifications",
        on_delete=models.CASCADE,
    )
    text = models.CharField(max_length=160)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["game", "recipient", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"Notification in game #{self.game_id}: {self.text}"


class PlayerStats(models.Model):
    """Aggregated player statistics for future leaderboards and profiles."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name="backgammon_stats",
        on_delete=models.CASCADE,
    )
    games_played = models.PositiveIntegerField(default=0)
    wins = models.PositiveIntegerField(default=0)
    losses = models.PositiveIntegerField(default=0)
    oin_wins = models.PositiveIntegerField(default=0)
    mars_wins = models.PositiveIntegerField(default=0)
    oin_losses = models.PositiveIntegerField(default=0)
    mars_losses = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "player stats"

    def __str__(self) -> str:
        return f"Stats for {self.user.username}"

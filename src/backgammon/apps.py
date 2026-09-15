from django.apps import AppConfig


class BackgammonConfig(AppConfig):
    """Django application configuration for the backgammon app."""

    name = "backgammon"

    def ready(self) -> None:
        """Register the game's project-level metadata adapter."""
        from . import game_type  # noqa: F401

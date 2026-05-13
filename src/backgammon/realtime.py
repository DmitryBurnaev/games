from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def game_group_name(game_id: int) -> str:
    """Return the Channels group name for one game board."""
    return f"backgammon_game_{game_id}"


def notify_game_updated(game_id: int) -> None:
    """Ask connected board consumers to push fresh viewer-specific state."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        async_to_sync(channel_layer.group_send)(
            game_group_name(game_id),
            {"type": "game.updated"},
        )
    except Exception:
        logger.exception("Failed to publish realtime game update for game %s", game_id)

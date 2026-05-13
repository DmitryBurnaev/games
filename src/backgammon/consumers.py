from __future__ import annotations

from typing import Any

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .models import Game
from .realtime import game_group_name
from .services import serialize_game


class GameStateConsumer(AsyncJsonWebsocketConsumer):
    """Push viewer-specific game state updates to one board page."""

    game_id: int
    group_name: str

    async def connect(self) -> None:
        """Accept authorized viewers and send the initial game snapshot."""
        self.game_id = int(self.scope["url_route"]["kwargs"]["game_id"])
        self.group_name = game_group_name(self.game_id)
        user = self.scope["user"]

        if not user.is_authenticated or not await self.can_view_game(user):
            await self.close()
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_game_state()

    async def disconnect(self, close_code: int) -> None:
        """Remove the socket from the game group when it closes."""
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content: dict[str, Any], **kwargs: Any) -> None:
        """Handle browser heartbeat messages."""
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def game_updated(self, event: dict[str, Any]) -> None:
        """Send fresh state after a backend mutation publishes an update."""
        await self.send_game_state()

    async def send_game_state(self) -> None:
        """Serialize and send state for this connection's viewer."""
        payload = await self.serialize_for_viewer()
        if payload is None:
            await self.close()
            return
        await self.send_json({"type": "game_state", "game": payload})

    @database_sync_to_async
    def can_view_game(self, user: Any) -> bool:
        """Return whether the user may subscribe to this game."""
        game = self.get_game()
        if game is None:
            return False
        return self.can_view_game_instance(game, user)

    @database_sync_to_async
    def serialize_for_viewer(self) -> dict[str, Any] | None:
        """Return a viewer-specific game payload, or None when access is gone."""
        game = self.get_game()
        if game is None:
            return None
        if not self.can_view_game_instance(game, self.scope["user"]):
            return None
        return serialize_game(game, self.scope["user"])

    def get_game(self) -> Game | None:
        """Fetch the subscribed game with related player objects."""
        try:
            return Game.objects.select_related(
                "white_player", "black_player", "current_player", "winner"
            ).get(pk=self.game_id)
        except Game.DoesNotExist:
            return None

    def can_view_game_instance(self, game: Game, user: Any) -> bool:
        """Return whether the user may currently view this game instance."""
        return game.status == Game.Status.WAITING or bool(game.color_for(user))

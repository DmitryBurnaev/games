from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from .models import Game, GameMove
from .services import serialize_game


@override_settings(BACKGAMMON_DEBUG_TOOLS=False)
class FinishedStatisticsCacheTests(TestCase):
    """Finished summaries avoid reloading a long immutable move history."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.white = user_model.objects.create_user(username="white")
        self.black = user_model.objects.create_user(username="black")
        self.game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            winner=self.white,
            status=Game.Status.FINISHED,
            victory_type=Game.VictoryType.OIN,
            finished_dice_statistics={
                "white": {
                    "total_points": 7,
                    "double_rolls": 0,
                    "double_moves_used": 0,
                    "double_moves_available": 0,
                },
                "black": {
                    "total_points": 0,
                    "double_rolls": 0,
                    "double_moves_used": 0,
                    "double_moves_available": 0,
                },
            },
            finished_skipped_statistics={
                "white": {"turns": 0, "moves": 0, "points": 0},
                "black": {"turns": 0, "moves": 0, "points": 0},
            },
        )
        GameMove.objects.bulk_create(
            [
                GameMove(
                    game=self.game,
                    player=self.white,
                    action=GameMove.Action.ROLL,
                    dice=[3, 4],
                )
                for _ in range(1000)
            ]
        )

    def test_finished_projection_uses_stored_statistics_without_history_query(
        self,
    ) -> None:
        game = Game.objects.select_related(
            "white_player",
            "black_player",
            "planned_opponent",
            "current_player",
            "winner",
        ).get(pk=self.game.pk)

        with CaptureQueriesContext(connection) as queries:
            payload = serialize_game(game, self.white)

        self.assertEqual(payload["dice_statistics"]["white"]["total_points"], 7)
        self.assertNotIn(
            "backgammon_gamemove", "\n".join(query["sql"] for query in queries)
        )

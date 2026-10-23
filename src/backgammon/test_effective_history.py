from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from .models import Game, GameMove, GamePositionSet
from .services import (
    apply_move,
    arrange_checkers_in_home,
    effective_game_history,
    game_history,
    record_game_event,
    serialize_game,
    undo_last_move,
)


@override_settings(BACKGAMMON_DEBUG_TOOLS=True)
class EffectiveGameHistoryTests(TestCase):
    """Focused coverage for append-only audit rows and effective projections."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.white = user_model.objects.create_user(username="white")
        self.black = user_model.objects.create_user(username="black")
        self.game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            status=Game.Status.ACTIVE,
            dice=[1, 2],
            remaining_moves=[1, 2],
            has_rolled=True,
        )
        record_game_event(
            self.game,
            self.white,
            GameMove.Action.ROLL,
            dice=[1, 2],
        )

    def test_undo_keeps_audit_snapshot_but_removes_move_from_effective_history(
        self,
    ) -> None:
        """Undo is ordered data, not deletion of the original board snapshot."""
        apply_move(self.game, self.white, 0, 1)
        move = self.game.moves.get(action=GameMove.Action.MOVE)

        undo_last_move(self.game, self.white)
        self.game.refresh_from_db()
        undo = self.game.moves.get(action=GameMove.Action.UNDO)
        effective = effective_game_history(game_history(self.game))
        payload = serialize_game(self.game, self.white)

        self.assertEqual(undo.reverted_move_id, move.pk)
        self.assertEqual(move.board[1], {"color": Game.Color.WHITE, "count": 1})
        self.assertNotIn(move.pk, [event["pk"] for event in effective])
        self.assertFalse(payload["can_undo"])
        self.assertEqual(payload["last_move_steps"], [])
        self.assertEqual(
            [entry["action"] for entry in payload["debug_move_history"]],
            [GameMove.Action.ROLL],
        )

    def test_position_setup_creates_turn_boundary_without_erasing_audit_rows(
        self,
    ) -> None:
        """A setup invalidates old current-turn markers while retaining snapshots."""
        apply_move(self.game, self.white, 0, 1)
        original_move = self.game.moves.get(action=GameMove.Action.MOVE)

        arrange_checkers_in_home(self.game, self.white)
        self.game.refresh_from_db()
        effective = effective_game_history(game_history(self.game))
        payload = serialize_game(self.game, self.white)

        boundary = self.game.position_sets.get()
        self.assertTrue(GameMove.objects.filter(pk=original_move.pk).exists())
        self.assertEqual(boundary.metadata["preset"], "prepare_bear_off")
        self.assertFalse(boundary.metadata["reset_statistics"])
        self.assertEqual(effective[-1]["action"], GameMove.Action.POSITION_SET)
        self.assertEqual(payload["last_move_steps"], [])
        self.assertFalse(payload["can_undo"])

    def test_statistics_reset_setup_keeps_only_new_effective_game_events(self) -> None:
        """Legacy delete-and-seed helpers keep their old statistics semantics."""
        apply_move(self.game, self.white, 0, 1)
        old_event_ids = list(self.game.moves.values_list("pk", flat=True))

        from .services import arrange_final_double_test

        arrange_final_double_test(self.game, self.white)
        effective = effective_game_history(game_history(self.game))
        payload = serialize_game(self.game, self.white)

        self.assertEqual(self.game.moves.count(), len(old_event_ids) + 1)
        self.assertEqual(GamePositionSet.objects.filter(game=self.game).count(), 1)
        self.assertEqual(
            GameMove.objects.filter(pk__in=old_event_ids).count(), len(old_event_ids)
        )
        self.assertEqual(
            [event["action"] for event in effective],
            [GameMove.Action.POSITION_SET, GameMove.Action.ROLL],
        )
        self.assertEqual(payload["dice_statistics"]["white"]["total_points"], 16)

    def test_legacy_rows_without_effective_history_fields_remain_compatible(
        self,
    ) -> None:
        """Rows created before the migration retain their existing projection meaning."""
        legacy_game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            status=Game.Status.ACTIVE,
            dice=[3, 4],
            remaining_moves=[4],
            has_rolled=True,
        )
        GameMove.objects.create(
            game=legacy_game,
            player=self.white,
            action=GameMove.Action.ROLL,
            dice=[3, 4],
        )
        GameMove.objects.create(
            game=legacy_game,
            player=self.white,
            action=GameMove.Action.MOVE,
            dice=[3, 4],
            distance=3,
        )

        payload = serialize_game(legacy_game, self.white)

        self.assertEqual(payload["dice_statistics"]["white"]["total_points"], 7)
        self.assertEqual(payload["last_move_steps"][0]["distance"], 3)

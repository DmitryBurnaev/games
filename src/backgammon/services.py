from __future__ import annotations

import random
from typing import Any

from django.db.models import F, QuerySet

from .models import Board, Game, GameMove, PlayerStats

PlayerPayload = dict[str, Any] | None
MovePayload = dict[str, Any]
MarkerPayload = dict[str, Any]


PATHS: dict[Game.Color, list[int]] = {
    Game.Color.WHITE: list(range(24)),
    Game.Color.BLACK: list(range(12, 24)) + list(range(12)),
}
HEADS: dict[Game.Color, int] = {
    Game.Color.WHITE: 0,
    Game.Color.BLACK: 12,
}
HOME_START = 18


class GameError(ValueError):
    """Raised when a requested game action violates the rules."""


def opponent_color(color: Game.Color) -> Game.Color:
    """Return the opposite checker color."""
    return Game.Color.BLACK if color == Game.Color.WHITE else Game.Color.WHITE


def point_label(index: int) -> int:
    """Return the human-readable one-based point number."""
    return index + 1


def color_name(color: Game.Color) -> str:
    """Return the Russian display name for a checker color."""
    return "Белые" if color == Game.Color.WHITE else "Черные"


def player_payload(user: Any) -> PlayerPayload:
    """Serialize a user for JSON responses without exposing extra fields."""
    if not user:
        return None
    return {"id": user.id, "username": user.username}


def path_position(color: Game.Color, point: int) -> int:
    """Return a point's zero-based position in a color's movement path."""
    return PATHS[color].index(point)


def point_has_color(board: Board, point: int, color: Game.Color) -> bool:
    """Check whether a board point contains at least one checker of a color."""
    stack = board[point]
    return bool(stack and stack.get("color") == color and stack.get("count", 0) > 0)


def all_checkers_in_home(
    board: Board,
    borne_off: dict[str, int],
    color: Game.Color,
) -> bool:
    """Return whether all remaining checkers of a color are in its home board."""
    if borne_off.get(color, 0) == 15:
        return True
    for point in range(24):
        if (
            point_has_color(board, point, color)
            and path_position(color, point) < HOME_START
        ):
            return False
    return True


def max_distance_to_bear_off(board: Board, color: Game.Color) -> int:
    """Return the farthest bear-off distance among checkers in home."""
    distances: list[int] = []
    for point in range(24):
        if point_has_color(board, point, color):
            position = path_position(color, point)
            if position >= HOME_START:
                distances.append(24 - position)
    return max(distances) if distances else 0


def allowed_head_moves(game: Game) -> int:
    """Return how many checkers may leave the head this turn."""
    if (
        game.turn_number == 1
        and len(game.dice) == 2
        and game.dice[0] == game.dice[1]
        and game.dice[0] in [3, 4, 6]
    ):
        return 2
    return 1


def validate_block_rule(board: Board, color: Game.Color) -> None:
    """Reject illegal six-checker blocks that fully trap the opponent."""
    path = PATHS[color]
    opponent = opponent_color(color)
    occupied = [point_has_color(board, point, color) for point in path]

    for start in range(24):
        if not all(occupied[(start + offset) % 24] for offset in range(6)):
            continue
        has_opponent_ahead = any(
            point_has_color(board, path[position % 24], opponent)
            for position in range(start + 6, start + 24)
        )
        if not has_opponent_ahead:
            raise GameError(
                "Нельзя строить блок из 6 шашек, если впереди блока нет шашки соперника."
            )


def clone_board(board: Board) -> Board:
    """Return a shallow copy of the JSON board stacks."""
    return [stack.copy() if stack else None for stack in board]


def remove_checker(board: Board, point: int) -> None:
    """Remove one checker from a point, clearing the point when empty."""
    stack = board[point]
    if not stack:
        raise GameError("На выбранном пункте нет шашки.")
    stack["count"] -= 1
    if stack["count"] <= 0:
        board[point] = None


def add_checker(board: Board, point: int, color: Game.Color) -> None:
    """Add one checker of a color to a point."""
    stack = board[point]
    if stack:
        stack["count"] += 1
    else:
        board[point] = {"color": color, "count": 1}


def remove_color_from_board(board: Board, color: Game.Color) -> int:
    """Remove all checkers of a color from the board and return their count."""
    removed = 0
    for point, stack in enumerate(board):
        if stack and stack.get("color") == color:
            removed += stack.get("count", 0)
            board[point] = None
    return removed


def home_points(color: Game.Color) -> list[int]:
    """Return board points that form a color's home area."""
    return PATHS[color][HOME_START:]


def has_six_point_block_outside_home(board: Board, color: Game.Color) -> bool:
    """Return whether a color has six consecutive occupied points outside home."""
    path = PATHS[color]
    home = set(home_points(color))
    for start in range(24):
        points = [path[(start + offset) % 24] for offset in range(6)]
        if any(point in home for point in points):
            continue
        if all(point_has_color(board, point, color) for point in points):
            return True
    return False


def surrender_mars_available(game: Game, loser: Any) -> bool:
    """Return whether a surrendering player may mark the loss as mars."""
    loser_color = game.color_for(loser)
    if not loser_color:
        return False
    winner_color = opponent_color(loser_color)
    return has_six_point_block_outside_home(game.board, winner_color)


def arrange_checkers_in_home(game: Game, user: Any) -> None:
    """Move all of a user's remaining checkers into home for finish testing."""
    if game.status != Game.Status.ACTIVE:
        raise GameError("Раскладку можно подготовить только в активной игре.")

    color = game.color_for(user)
    if not color:
        raise GameError("Вы не участвуете в этой игре.")

    remaining = 15 - game.borne_off.get(color, 0)
    remove_color_from_board(game.board, color)
    available_home_points = [
        point
        for point in home_points(color)
        if not game.board[point] or game.board[point].get("color") == color
    ]
    if not available_home_points:
        raise GameError("В доме нет свободных пунктов для тестовой раскладки.")
    for offset in range(remaining):
        add_checker(
            game.board,
            available_home_points[offset % len(available_home_points)],
            color,
        )

    game.current_player = user
    game.dice = []
    game.remaining_moves = []
    game.has_rolled = False
    game.head_moves_this_turn = 0
    game.save()


def arrange_checkers_for_victory_test(game: Game, user: Any) -> None:
    """Leave one or two user checkers in home and mark the rest borne off."""
    if game.status != Game.Status.ACTIVE:
        raise GameError("Тест победы можно подготовить только в активной игре.")

    color = game.color_for(user)
    if not color:
        raise GameError("Вы не участвуете в этой игре.")

    remove_color_from_board(game.board, color)
    available_home_points = [
        point
        for point in home_points(color)
        if not game.board[point] or game.board[point].get("color") == color
    ]
    if len(available_home_points) < 2:
        raise GameError("В доме нет двух свободных пунктов для теста победы.")

    game.borne_off[color] = 13
    add_checker(game.board, available_home_points[-2], color)
    add_checker(game.board, available_home_points[-1], color)
    game.current_player = user
    game.dice = []
    game.remaining_moves = []
    game.has_rolled = False
    game.head_moves_this_turn = 0
    game.save()


def validate_move(
    game: Game,
    user: Any,
    source_point: int | None,
    distance: int,
) -> MovePayload:
    """Validate a move request and return the resulting action metadata."""
    if game.status != Game.Status.ACTIVE:
        raise GameError("Игра еще не активна или уже завершена.")
    if game.current_player_id != user.id:
        raise GameError("Сейчас ход другого игрока.")
    if not game.has_rolled:
        raise GameError("Сначала бросьте кубики.")
    if distance not in game.remaining_moves:
        raise GameError("Такого значения уже нет среди доступных ходов.")
    if source_point is None or source_point < 0 or source_point > 23:
        raise GameError("Некорректный пункт.")

    color = game.color_for(user)
    if not color:
        raise GameError("Вы не участвуете в этой игре.")
    if not point_has_color(game.board, source_point, color):
        raise GameError("На выбранном пункте нет вашей шашки.")

    if source_point == HEADS[color] and game.head_moves_this_turn >= allowed_head_moves(
        game
    ):
        raise GameError("За один ход можно снять с головы только одну шашку.")

    position = path_position(color, source_point)
    target_position = position + distance

    if target_position < 24:
        target_point = PATHS[color][target_position]
        target_stack = game.board[target_point]
        if target_stack and target_stack.get("color") != color:
            raise GameError("Нельзя ставить шашку на пункт, занятый соперником.")

        candidate = clone_board(game.board)
        remove_checker(candidate, source_point)
        add_checker(candidate, target_point, color)
        validate_block_rule(candidate, color)
        return {"action": GameMove.Action.MOVE, "target_point": target_point}

    if not all_checkers_in_home(game.board, game.borne_off, color):
        raise GameError("Выбрасывать шашки можно только когда все ваши шашки в доме.")
    if position < HOME_START:
        raise GameError("Эта шашка еще не в доме.")

    distance_to_off = 24 - position
    max_distance = max_distance_to_bear_off(game.board, color)
    if distance == distance_to_off or (
        distance > distance_to_off and distance_to_off == max_distance
    ):
        return {"action": GameMove.Action.BEAR_OFF, "target_point": None}

    raise GameError("Этим кубиком нельзя выбросить выбранную шашку.")


def legal_moves(game: Game, user: Any) -> list[MovePayload]:
    """Return all currently legal checker moves for a user."""
    color = game.color_for(user)
    if not color or game.current_player_id != user.id or not game.has_rolled:
        return []

    moves = []
    for source_point in range(24):
        if not point_has_color(game.board, source_point, color):
            continue
        for distance in sorted(set(game.remaining_moves)):
            try:
                move = validate_move(game, user, source_point, distance)
            except GameError:
                continue
            moves.append(
                {
                    "source": source_point,
                    "source_label": point_label(source_point),
                    "target": move["target_point"],
                    "target_label": (
                        point_label(move["target_point"])
                        if move["target_point"] is not None
                        else None
                    ),
                    "distance": distance,
                    "action": move["action"],
                }
            )
    return moves


def checker_moves_for_current_roll(game: Game, user: Any) -> QuerySet[GameMove]:
    """Return checker moves made by a user since their latest roll."""
    latest_roll = last_roll_for(game, user)
    queryset = game.moves.filter(
        player=user,
        action__in=[GameMove.Action.MOVE, GameMove.Action.BEAR_OFF],
    )
    if latest_roll:
        queryset = queryset.filter(pk__gt=latest_roll.pk)
    return queryset.order_by("created_at", "pk")


def move_marker_player(game: Game, viewer: Any) -> Any | None:
    """Return whose moved checkers should be highlighted for a viewer."""
    if game.status != Game.Status.ACTIVE or not game.color_for(viewer):
        return None

    if game.current_player_id == viewer.id:
        return viewer if game.has_rolled else game.opponent_for(viewer)
    if game.has_rolled:
        return game.current_player
    return None


def last_move_markers(game: Game, viewer: Any) -> list[MarkerPayload]:
    """Group all moved-checker highlights visible to the viewer."""
    marker_player = move_marker_player(game, viewer)
    if not marker_player:
        return []

    color = game.color_for(marker_player)
    markers_by_target: dict[int, MarkerPayload] = {}
    for move in checker_moves_for_current_roll(game, marker_player):
        if move.target_point is None:
            continue
        marker = markers_by_target.setdefault(
            move.target_point,
            {
                "player": player_payload(marker_player),
                "color": color,
                "target": move.target_point,
                "count": 0,
                "moves": [],
            },
        )
        marker["count"] += 1
        marker["moves"].append(
            {
                "id": move.id,
                "source": move.source_point,
                "target": move.target_point,
                "distance": move.distance,
                "action": move.action,
            }
        )

    return list(markers_by_target.values())


def last_move_steps(game: Game, viewer: Any) -> list[MovePayload]:
    """Return visible checker moves in chronological order for UI animation."""
    marker_player = move_marker_player(game, viewer)
    if not marker_player:
        return []

    color = game.color_for(marker_player)
    return [
        {
            "id": move.id,
            "player": player_payload(marker_player),
            "color": color,
            "source": move.source_point,
            "target": move.target_point,
            "distance": move.distance,
            "action": move.action,
        }
        for move in checker_moves_for_current_roll(game, marker_player)
    ]


def last_move_marker(game: Game, viewer: Any) -> MarkerPayload | None:
    """Return the final visible move marker for legacy frontend callers."""
    markers = last_move_markers(game, viewer)
    return markers[-1] if markers else None


def serialize_game(game: Game, viewer: Any) -> dict[str, Any]:
    """Serialize a game into the JSON shape consumed by the browser UI."""
    viewer_color = game.color_for(viewer)
    viewer_moves = legal_moves(game, viewer)
    return {
        "id": game.id,
        "status": game.status,
        "victory_type": game.victory_type,
        "white_player": player_payload(game.white_player),
        "black_player": player_payload(game.black_player),
        "current_player": player_payload(game.current_player),
        "winner": player_payload(game.winner),
        "viewer_color": viewer_color,
        "viewer_color_name": color_name(viewer_color) if viewer_color else None,
        "board": game.board,
        "borne_off": game.borne_off,
        "dice": game.dice,
        "remaining_moves": game.remaining_moves,
        "has_rolled": game.has_rolled,
        "turn_number": game.turn_number,
        "can_surrender": game.status == Game.Status.ACTIVE
        and bool(viewer_color)
        and bool(game.opponent_for(viewer)),
        "surrender_mars_available": game.status == Game.Status.ACTIVE
        and surrender_mars_available(game, viewer),
        "can_roll": game.status == Game.Status.ACTIVE
        and game.current_player_id == viewer.id
        and not game.has_rolled,
        "can_end_turn": (
            game.status == Game.Status.ACTIVE
            and game.current_player_id == viewer.id
            and game.has_rolled
            and not viewer_moves
        ),
        "can_undo": can_undo_last_move(game, viewer),
        "last_move_marker": last_move_marker(game, viewer),
        "last_move_markers": last_move_markers(game, viewer),
        "last_move_steps": last_move_steps(game, viewer),
        "legal_moves": viewer_moves,
    }


def can_undo_last_move(game: Game, user: Any) -> bool:
    """Return whether a viewer may undo their latest move in this turn."""
    if (
        game.status != Game.Status.ACTIVE
        or game.current_player_id != user.id
        or not game.has_rolled
    ):
        return False
    latest_move = game.moves.order_by("-created_at", "-pk").first()
    return bool(
        latest_move
        and latest_move.player_id == user.id
        and latest_move.action in [GameMove.Action.MOVE, GameMove.Action.BEAR_OFF]
    )


def create_roll(game: Game, user: Any) -> list[int]:
    """Roll dice for the current player and persist available moves."""
    if game.status != Game.Status.ACTIVE:
        raise GameError("Игра еще не активна или уже завершена.")
    if game.current_player_id != user.id:
        raise GameError("Сейчас ход другого игрока.")
    if game.has_rolled:
        raise GameError("Кубики уже брошены.")

    dice = [random.randint(1, 6), random.randint(1, 6)]
    game.dice = dice
    game.remaining_moves = [dice[0]] * 4 if dice[0] == dice[1] else dice[:]
    game.has_rolled = True
    game.save(update_fields=["dice", "remaining_moves", "has_rolled", "updated_at"])
    GameMove.objects.create(
        game=game, player=user, action=GameMove.Action.ROLL, dice=dice, board=game.board
    )
    return dice


def switch_turn(game: Game) -> None:
    """Advance the game to the opponent and clear turn-local state."""
    next_player = game.opponent_for(game.current_player)
    game.current_player = next_player
    game.dice = []
    game.remaining_moves = []
    game.has_rolled = False
    game.head_moves_this_turn = 0
    game.turn_number += 1


def update_stats(game: Game, winner: Any) -> None:
    """Increment aggregate stats after a finished game."""
    loser = game.opponent_for(winner)
    winner_stats, _ = PlayerStats.objects.get_or_create(user=winner)
    loser_stats, _ = PlayerStats.objects.get_or_create(user=loser)

    PlayerStats.objects.filter(pk=winner_stats.pk).update(
        games_played=F("games_played") + 1,
        wins=F("wins") + 1,
        oin_wins=F("oin_wins")
        + (1 if game.victory_type == Game.VictoryType.OIN else 0),
        mars_wins=F("mars_wins")
        + (1 if game.victory_type == Game.VictoryType.MARS else 0),
    )
    PlayerStats.objects.filter(pk=loser_stats.pk).update(
        games_played=F("games_played") + 1,
        losses=F("losses") + 1,
        oin_losses=F("oin_losses")
        + (1 if game.victory_type == Game.VictoryType.OIN else 0),
        mars_losses=F("mars_losses")
        + (1 if game.victory_type == Game.VictoryType.MARS else 0),
    )


def surrender_game(
    game: Game, loser: Any, requested_victory_type: str | None = None
) -> None:
    """Finish an active game when a player resigns."""
    if game.status != Game.Status.ACTIVE:
        raise GameError("Сдаться можно только в активной игре.")
    if not game.color_for(loser):
        raise GameError("Вы не участвуете в этой игре.")
    winner = game.opponent_for(loser)
    if not winner:
        raise GameError("У этой игры еще нет соперника.")

    victory_type = Game.VictoryType.OIN
    if requested_victory_type == Game.VictoryType.MARS:
        if not surrender_mars_available(game, loser):
            raise GameError("Марс при сдаче доступен только при блоке из 6 пунктов.")
        victory_type = Game.VictoryType.MARS

    game.mark_finished(winner, victory_type)
    game.dice = []
    game.remaining_moves = []
    game.has_rolled = False
    update_stats(game, winner)
    game.save()
    GameMove.objects.create(
        game=game,
        player=loser,
        action=GameMove.Action.FINISH,
        dice=[],
        board=game.board,
    )


def apply_move(game: Game, user: Any, source_point: int, distance: int) -> None:
    """Apply one validated checker move or bear-off to the board."""
    color = game.color_for(user)
    move = validate_move(game, user, source_point, distance)
    dice_before_move = game.dice[:]

    remove_checker(game.board, source_point)
    if move["action"] == GameMove.Action.BEAR_OFF:
        game.borne_off[color] = game.borne_off.get(color, 0) + 1
    else:
        add_checker(game.board, move["target_point"], color)

    game.remaining_moves.remove(distance)
    if source_point == HEADS[color]:
        game.head_moves_this_turn += 1

    if game.borne_off.get(color, 0) >= 15:
        loser_color = opponent_color(color)
        victory_type = (
            Game.VictoryType.MARS
            if game.borne_off.get(loser_color, 0) == 0
            else Game.VictoryType.OIN
        )
        game.mark_finished(user, victory_type)
        update_stats(game, user)

    game.save()
    GameMove.objects.create(
        game=game,
        player=user,
        action=move["action"],
        dice=dice_before_move,
        source_point=source_point,
        target_point=move["target_point"],
        distance=distance,
        board=game.board,
    )

    if game.status == Game.Status.FINISHED:
        GameMove.objects.create(
            game=game,
            player=user,
            action=GameMove.Action.FINISH,
            dice=game.dice,
            board=game.board,
        )


def last_roll_for(game: Game, user: Any) -> GameMove | None:
    """Return the latest roll event recorded for a user in a game."""
    return (
        game.moves.filter(game=game, player=user, action=GameMove.Action.ROLL)
        .order_by("-created_at", "-pk")
        .first()
    )


def restore_head_moves_count(game: Game, user: Any, undone_move: GameMove) -> None:
    """Recompute how many head moves remain after undoing one move."""
    color = game.color_for(user)
    latest_roll = last_roll_for(game, user)
    if not latest_roll:
        game.head_moves_this_turn = 0
        return

    game.head_moves_this_turn = (
        game.moves.filter(
            game=game,
            player=user,
            action__in=[GameMove.Action.MOVE, GameMove.Action.BEAR_OFF],
            source_point=HEADS[color],
            pk__gt=latest_roll.pk,
        )
        .exclude(pk=undone_move.pk)
        .count()
    )


def undo_last_move(game: Game, user: Any) -> None:
    """Undo the user's latest checker move during their unfinished turn."""
    if game.status != Game.Status.ACTIVE:
        raise GameError("Завершенную игру нельзя откатить.")
    if game.current_player_id != user.id or not game.has_rolled:
        raise GameError("Отменить ход можно только до завершения вашего текущего хода.")
    latest_move = game.moves.order_by("-created_at", "-pk").first()
    if not latest_move or latest_move.player_id != user.id:
        raise GameError("Отменить можно только ваш последний ход.")
    if latest_move.action not in [GameMove.Action.MOVE, GameMove.Action.BEAR_OFF]:
        raise GameError("Последнее действие нельзя отменить.")

    color = game.color_for(user)
    if not color:
        raise GameError("Вы не участвуете в этой игре.")

    if not game.dice:
        latest_roll = last_roll_for(game, user)
        game.dice = latest_move.dice or (
            latest_roll.dice if latest_roll else [latest_move.distance]
        )

    if latest_move.action == GameMove.Action.BEAR_OFF:
        if game.borne_off.get(color, 0) <= 0:
            raise GameError("Не удалось отменить выброс шашки.")
        game.borne_off[color] -= 1
        add_checker(game.board, latest_move.source_point, color)
    else:
        if latest_move.target_point is None or not point_has_color(
            game.board, latest_move.target_point, color
        ):
            raise GameError("Не удалось найти шашку для отката.")
        remove_checker(game.board, latest_move.target_point)
        add_checker(game.board, latest_move.source_point, color)

    game.remaining_moves.append(latest_move.distance)
    restore_head_moves_count(game, user, latest_move)
    latest_move.delete()
    game.save()


def finish_blocked_turn(game: Game, user: Any) -> None:
    """Finish the current turn when the player has no legal moves left."""
    if game.status != Game.Status.ACTIVE:
        raise GameError("Игра еще не активна или уже завершена.")
    if game.current_player_id != user.id:
        raise GameError("Сейчас ход другого игрока.")
    if not game.has_rolled:
        raise GameError("Кубики еще не брошены.")
    if legal_moves(game, user):
        raise GameError("У вас еще есть допустимые ходы.")

    switch_turn(game)
    game.save()

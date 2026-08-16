from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from django.db.models import F, QuerySet
from django.utils import timezone

from .app_settings import (
    DICE_MODE_PLAYER_BAG,
    backgammon_dice_mode,
    backgammon_notification_display_ms,
    backgammon_quick_notifications_enabled,
)
from .models import (
    Board,
    Game,
    GameMove,
    GameNotification,
    PlayerStats,
    QuickNotificationPreset,
)

PlayerPayload = dict[str, Any] | None
MovePayload = dict[str, Any]
MarkerPayload = dict[str, Any]
NotificationPayload = dict[str, Any]


PATHS: dict[Game.Color, list[int]] = {
    Game.Color.WHITE: list(range(24)),
    Game.Color.BLACK: list(range(12, 24)) + list(range(12)),
}
HEADS: dict[Game.Color, int] = {
    Game.Color.WHITE: 0,
    Game.Color.BLACK: 12,
}
HOME_START = 18
DICE_PAIR_BAG: tuple[tuple[int, int], ...] = tuple(
    (left, right) for left in range(1, 7) for right in range(1, 7)
)


class GameError(ValueError):
    """Raised when a requested game action violates the rules."""


def roll_die() -> int:
    """Return one fair die value using the operating system RNG."""
    return secrets.randbelow(6) + 1


def roll_dice() -> list[int]:
    """Return two independent fair dice values."""
    return [roll_die(), roll_die()]


def roll_dice_from_player_bag(game: Game, user: Any) -> list[int]:
    """Return dice from a per-player bag of all 36 ordered dice pairs."""
    previous_rolls = list(
        game.moves.filter(player=user, action=GameMove.Action.ROLL)
        .order_by("created_at", "pk")
        .values_list("dice", flat=True)
    )
    previous_pairs = [
        tuple(dice)
        for dice in previous_rolls
        if isinstance(dice, list) and len(dice) == 2
    ]
    cycle_size = len(previous_pairs) % len(DICE_PAIR_BAG)
    current_cycle = previous_pairs[-cycle_size:] if cycle_size else []
    remaining = list(DICE_PAIR_BAG)
    for pair in current_cycle:
        if pair in remaining:
            remaining.remove(pair)
    return list(secrets.choice(remaining or list(DICE_PAIR_BAG)))


def roll_dice_for_game(game: Game, user: Any) -> list[int]:
    """Return dice using the configured game dice mode."""
    if backgammon_dice_mode() == DICE_MODE_PLAYER_BAG:
        return roll_dice_from_player_bag(game, user)
    return roll_dice()


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
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.get_full_name() or user.username,
    }


def datetime_payload(value: Any) -> str | None:
    """Serialize datetimes for the browser without assuming local timezone."""
    return value.isoformat() if value else None


def terminal_roll_for_statistics(game: Game) -> GameMove | None:
    """Return the winning roll, which is excluded from finished-game statistics."""
    if game.status != Game.Status.FINISHED:
        return None

    finish = (
        game.moves.filter(action=GameMove.Action.FINISH)
        .order_by("-created_at", "-pk")
        .first()
    )
    if not finish or not finish.dice:
        return None
    return (
        game.moves.filter(
            action=GameMove.Action.ROLL,
            player=finish.player,
            pk__lt=finish.pk,
        )
        .order_by("-created_at", "-pk")
        .first()
    )


def dice_statistics_by_color(game: Game) -> dict[str, dict[str, int]]:
    """Return rolled points and double usage grouped by checker color."""
    statistics = {
        color: {
            "total_points": 0,
            "double_rolls": 0,
            "double_moves_used": 0,
            "double_moves_available": 0,
        }
        for color in Game.Color.values
    }
    moves = game.moves.select_related("player")
    terminal_roll = terminal_roll_for_statistics(game)
    for move in moves.filter(action=GameMove.Action.ROLL):
        if terminal_roll and move.pk == terminal_roll.pk:
            continue
        if not isinstance(move.dice, list) or len(move.dice) != 2:
            continue
        if not all(isinstance(die, int) for die in move.dice):
            continue
        color = game.color_for(move.player)
        if not color:
            continue
        statistics[color]["total_points"] += sum(move.dice)
        if move.dice[0] == move.dice[1]:
            statistics[color]["double_rolls"] += 1
            statistics[color]["double_moves_available"] += 4

    for move in moves.filter(
        action__in=[GameMove.Action.MOVE, GameMove.Action.BEAR_OFF]
    ):
        if terminal_roll and move.pk > terminal_roll.pk:
            continue
        if not isinstance(move.dice, list) or not move.dice:
            continue
        if not all(isinstance(die, int) and die == move.dice[0] for die in move.dice):
            continue
        color = game.color_for(move.player)
        if color:
            statistics[color]["double_moves_used"] += 1
    return statistics


def skipped_statistics_by_color(game: Game) -> dict[str, dict[str, int]]:
    """Return unspent dice moves and points grouped by rolling color."""
    statistics = {
        color: {"turns": 0, "moves": 0, "points": 0} for color in Game.Color.values
    }
    current_roll = None
    used_distances: list[int] = []

    def count_unspent_dice(roll: GameMove | None) -> None:
        if not roll:
            return
        color = game.color_for(roll.player)
        if not color or not isinstance(roll.dice, list) or len(roll.dice) != 2:
            return
        if not all(isinstance(die, int) for die in roll.dice):
            return
        available_dice = (
            [roll.dice[0]] * 4 if roll.dice[0] == roll.dice[1] else roll.dice[:]
        )
        for distance in used_distances:
            if distance in available_dice:
                available_dice.remove(distance)
        statistics[color]["turns"] += len(available_dice)
        statistics[color]["moves"] += len(available_dice)
        statistics[color]["points"] += sum(available_dice)

    for move in game.moves.order_by("created_at", "pk"):
        if move.action == GameMove.Action.ROLL:
            count_unspent_dice(current_roll)
            current_roll = move
            used_distances = []
        elif (
            current_roll
            and move.player_id == current_roll.player_id
            and move.action in [GameMove.Action.MOVE, GameMove.Action.BEAR_OFF]
            and isinstance(move.distance, int)
        ):
            used_distances.append(move.distance)

    return statistics


def skipped_turns_by_color(game: Game) -> dict[str, int]:
    """Return unspent dice-move counts grouped by rolling color."""
    statistics = skipped_statistics_by_color(game)
    return {color: statistics[color]["turns"] for color in Game.Color.values}


def double_rolls_by_color(game: Game) -> dict[str, int]:
    """Return double-roll counts grouped by checker color."""
    statistics = dice_statistics_by_color(game)
    return {color: statistics[color]["double_rolls"] for color in Game.Color.values}


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
    checker_count: int,
) -> bool:
    """Return whether all remaining checkers of a color are in its home board."""
    if borne_off.get(color, 0) >= checker_count:
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


def is_first_turn_for_color(game: Game, user: Any) -> bool:
    """Return whether the current roll is the first turn for this user's color."""
    latest_roll = (
        game.moves.filter(player=user, action=GameMove.Action.ROLL)
        .order_by("-created_at", "-pk")
        .first()
    )
    if latest_roll:
        return not game.moves.filter(
            player=user,
            action=GameMove.Action.ROLL,
            pk__lt=latest_roll.pk,
        ).exists()
    return True


def base_allowed_head_moves(game: Game, user: Any) -> int:
    """Return the regular number of checkers allowed to leave the head."""
    if (
        is_first_turn_for_color(game, user)
        and len(game.dice) == 2
        and game.dice[0] == game.dice[1]
        and game.dice[0] in [3, 4, 6]
    ):
        return 2
    return 1


def allowed_head_moves(game: Game, user: Any) -> int:
    """Return how many checkers may leave the head this turn."""
    allowed = base_allowed_head_moves(game, user)
    if can_take_extra_head_checker(game, user, allowed):
        return allowed + 1
    return allowed


def blocking_event_points(board: Board, color: Game.Color) -> list[int]:
    """Return the first illegal six-point block for a color, if present."""
    path = PATHS[color]
    opponent = opponent_color(color)
    occupied = [point_has_color(board, point, color) for point in path]

    for start in range(24):
        points = [path[(start + offset) % 24] for offset in range(6)]
        if not all(occupied[(start + offset) % 24] for offset in range(6)):
            continue
        has_opponent_ahead = any(
            point_has_color(board, path[position % 24], opponent)
            for position in range(start + 6, start + 24)
        )
        if not has_opponent_ahead:
            return points
    return []


def has_blocking_event(board: Board, color: Game.Color) -> bool:
    """Return whether a color currently traps the opponent with a six-block."""
    return bool(blocking_event_points(board, color))


def validate_block_rule(board: Board, color: Game.Color) -> None:
    """Reject illegal six-checker blocks that fully trap the opponent."""
    if has_blocking_event(board, color):
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

    remaining = game.checker_count - game.borne_off.get(color, 0)
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
    home_checkers = min(2, game.checker_count)
    if len(available_home_points) < home_checkers:
        raise GameError("В доме нет свободных пунктов для теста победы.")

    game.borne_off[color] = game.checker_count - home_checkers
    for offset in range(home_checkers):
        add_checker(game.board, available_home_points[-(offset + 1)], color)
    game.current_player = user
    game.dice = []
    game.remaining_moves = []
    game.has_rolled = False
    game.head_moves_this_turn = 0
    game.save()


def arrange_final_double_test(game: Game, user: Any) -> None:
    """Prepare one checker for a winning 4/4 bear-off statistics test."""
    if game.status != Game.Status.ACTIVE:
        raise GameError(
            "Тест финального дубля можно подготовить только в активной игре."
        )

    color = game.color_for(user)
    if not color:
        raise GameError("Вы не участвуете в этой игре.")

    game.moves.all().delete()
    remove_color_from_board(game.board, color)
    game.borne_off[color] = game.checker_count - 1
    add_checker(game.board, PATHS[color][22], color)
    game.current_player = user
    game.dice = [4, 4]
    game.remaining_moves = [4, 4, 4, 4]
    game.has_rolled = True
    game.head_moves_this_turn = 0
    game.started_at = timezone.now()
    game.save()
    GameMove.objects.create(
        game=game,
        player=user,
        action=GameMove.Action.ROLL,
        dice=game.dice,
        board=game.board,
    )


def arrange_extra_head_move_test(game: Game, user: Any) -> None:
    """Prepare a first-turn position where only an extra head move is possible."""
    if game.status != Game.Status.ACTIVE:
        raise GameError("Тест головы можно подготовить только в активной игре.")

    color = game.color_for(user)
    if not color:
        raise GameError("Вы не участвуете в этой игре.")

    opponent = opponent_color(color)
    blocked_target = PATHS[color][10]

    game.moves.all().delete()
    game.board = [None for _ in range(24)]
    game.board[HEADS[color]] = {"color": color, "count": game.checker_count}
    if game.checker_count > 1:
        game.board[HEADS[opponent]] = {
            "color": opponent,
            "count": game.checker_count - 1,
        }
    if game.checker_count > 0:
        game.board[blocked_target] = {"color": opponent, "count": 1}
    game.borne_off = {Game.Color.WHITE: 0, Game.Color.BLACK: 0}
    game.current_player = user
    game.dice = [5, 5]
    game.remaining_moves = [5, 5, 5, 5]
    game.has_rolled = True
    game.head_moves_this_turn = 0
    game.turn_number = 1
    game.started_at = timezone.now()
    game.save()
    GameMove.objects.create(
        game=game,
        player=user,
        action=GameMove.Action.ROLL,
        dice=game.dice,
        board=game.board,
    )


def arrange_blocking_event_test(game: Game, user: Any) -> None:
    """Prepare a turn that cannot be finished until the user breaks a block."""
    if game.status != Game.Status.ACTIVE:
        raise GameError("Тест блока можно подготовить только в активной игре.")

    color = game.color_for(user)
    if not color:
        raise GameError("Вы не участвуете в этой игре.")

    opponent = opponent_color(color)
    path = PATHS[color]
    game.moves.all().delete()
    game.board = [None for _ in range(24)]
    block_size = min(6, game.checker_count)
    for point in path[:block_size]:
        game.board[point] = {"color": color, "count": 1}
    remaining = game.checker_count - block_size
    if remaining:
        game.board[path[10]] = {"color": color, "count": remaining}
    game.borne_off = {color: 0, opponent: game.checker_count}
    game.current_player = user
    game.dice = [1]
    game.remaining_moves = []
    game.has_rolled = True
    game.head_moves_this_turn = 0
    game.started_at = timezone.now()
    game.save()
    GameMove.objects.create(
        game=game,
        player=user,
        action=GameMove.Action.ROLL,
        dice=game.dice,
        board=game.board,
    )
    GameMove.objects.create(
        game=game,
        player=user,
        action=GameMove.Action.MOVE,
        dice=game.dice,
        source_point=path[4],
        target_point=path[5],
        distance=1,
        board=game.board,
    )


def validate_move(
    game: Game,
    user: Any,
    source_point: int | None,
    distance: int,
    enforce_head_limit: bool = True,
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

    if (
        enforce_head_limit
        and source_point == HEADS[color]
        and game.head_moves_this_turn >= allowed_head_moves(game, user)
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

    if not all_checkers_in_home(game.board, game.borne_off, color, game.checker_count):
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


def has_non_head_legal_move(game: Game, user: Any) -> bool:
    """Return whether the player can move any checker that is not on the head."""
    color = game.color_for(user)
    if not color:
        return False
    for source_point in range(24):
        if source_point == HEADS[color] or not point_has_color(
            game.board, source_point, color
        ):
            continue
        for distance in sorted(set(game.remaining_moves)):
            try:
                validate_move(game, user, source_point, distance)
            except GameError:
                continue
            return True
    return False


def can_take_extra_head_checker(
    game: Game, user: Any, regular_head_moves: int | None = None
) -> bool:
    """Return whether a first-turn block allows one extra checker from the head."""
    color = game.color_for(user)
    if not color or not is_first_turn_for_color(game, user):
        return False
    regular_head_moves = (
        regular_head_moves
        if regular_head_moves is not None
        else base_allowed_head_moves(game, user)
    )
    if regular_head_moves != 1 or game.head_moves_this_turn != regular_head_moves:
        return False
    if has_non_head_legal_move(game, user):
        return False
    if not point_has_color(game.board, HEADS[color], color):
        return False
    for distance in sorted(set(game.remaining_moves)):
        try:
            validate_move(
                game,
                user,
                HEADS[color],
                distance,
                enforce_head_limit=False,
            )
        except GameError:
            continue
        return True
    return False


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


def quick_notification_payload(
    notification: GameNotification,
) -> NotificationPayload:
    """Return a quick-notification payload for browser rendering."""
    return {
        "id": notification.id,
        "text": notification.text,
        "sender": player_payload(notification.sender),
        "created_at": datetime_payload(notification.created_at),
    }


def quick_notifications_for_viewer(
    game: Game, viewer: Any
) -> list[NotificationPayload]:
    """Return recent notifications addressed to the viewer."""
    if not backgammon_quick_notifications_enabled() or not game.color_for(viewer):
        return []
    display_ms = backgammon_notification_display_ms()
    visible_since = timezone.now() - timedelta(milliseconds=display_ms)
    notifications = (
        game.notifications.select_related("sender")
        .filter(recipient=viewer, created_at__gte=visible_since)
        .order_by("created_at", "pk")
    )
    return [quick_notification_payload(notification) for notification in notifications]


def quick_notification_options() -> list[dict[str, str]]:
    """Return admin-configured notification controls in display order."""
    return list(QuickNotificationPreset.objects.values("text", "emoji"))


def create_quick_notification(
    game: Game,
    sender: Any,
    text: str,
) -> GameNotification:
    """Persist a predefined quick notification for the sender's opponent."""
    if not backgammon_quick_notifications_enabled():
        raise GameError("Быстрые уведомления выключены.")
    if game.status != Game.Status.ACTIVE:
        raise GameError("Уведомления доступны только в активной игре.")
    if not game.color_for(sender):
        raise GameError("Вы не участвуете в этой игре.")
    recipient = game.opponent_for(sender)
    if not recipient:
        raise GameError("Некому отправить уведомление.")
    normalized_text = (text or "").strip()
    if not QuickNotificationPreset.objects.filter(text=normalized_text).exists():
        raise GameError("Неизвестное быстрое уведомление.")
    notification = GameNotification.objects.create(
        game=game,
        sender=sender,
        recipient=recipient,
        text=normalized_text,
    )
    game.save(update_fields=["updated_at"])
    return notification


def serialize_game(game: Game, viewer: Any) -> dict[str, Any]:
    """Serialize a game into the JSON shape consumed by the browser UI."""
    viewer_color = game.color_for(viewer)
    viewer_moves = legal_moves(game, viewer)
    dice_statistics = dice_statistics_by_color(game)
    skipped_statistics = skipped_statistics_by_color(game)
    viewer_blocking_event = (
        game.status == Game.Status.ACTIVE
        and game.current_player_id == viewer.id
        and game.has_rolled
        and bool(viewer_color)
        and has_blocking_event(game.board, viewer_color)
    )
    return {
        "id": game.id,
        "party_number": game.party_number,
        "status": game.status,
        "victory_type": game.victory_type,
        "white_player": player_payload(game.white_player),
        "black_player": player_payload(game.black_player),
        "planned_opponent": player_payload(game.planned_opponent),
        "current_player": player_payload(game.current_player),
        "winner": player_payload(game.winner),
        "viewer_color": viewer_color,
        "viewer_color_name": color_name(viewer_color) if viewer_color else None,
        "created_at": datetime_payload(game.created_at),
        "updated_at": datetime_payload(game.updated_at),
        "started_at": datetime_payload(game.started_at),
        "finished_at": datetime_payload(game.finished_at),
        "double_rolls": {
            color: dice_statistics[color]["double_rolls"] for color in Game.Color.values
        },
        "dice_statistics": dice_statistics,
        "skipped_turns": {
            color: skipped_statistics[color]["turns"] for color in Game.Color.values
        },
        "skipped_moves": {
            color: skipped_statistics[color]["moves"] for color in Game.Color.values
        },
        "skipped_points": {
            color: skipped_statistics[color]["points"] for color in Game.Color.values
        },
        "checker_count": game.checker_count,
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
        "blocking_event": viewer_blocking_event,
        "blocking_event_points": (
            blocking_event_points(game.board, viewer_color)
            if viewer_blocking_event
            else []
        ),
        "can_roll": game.status == Game.Status.ACTIVE
        and game.current_player_id == viewer.id
        and not game.has_rolled,
        "can_end_turn": (
            game.status == Game.Status.ACTIVE
            and game.current_player_id == viewer.id
            and game.has_rolled
            and not viewer_moves
            and not viewer_blocking_event
        ),
        "can_undo": can_undo_last_move(game, viewer),
        "can_send_quick_notifications": (
            backgammon_quick_notifications_enabled()
            and game.status == Game.Status.ACTIVE
            and bool(viewer_color)
            and bool(game.opponent_for(viewer))
        ),
        "notification_display_ms": backgammon_notification_display_ms(),
        "quick_notifications": quick_notifications_for_viewer(game, viewer),
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

    dice = roll_dice_for_game(game, user)
    game.dice = dice
    game.remaining_moves = [dice[0]] * 4 if dice[0] == dice[1] else dice[:]
    game.has_rolled = True
    update_fields = ["dice", "remaining_moves", "has_rolled", "updated_at"]
    if game.started_at is None:
        game.started_at = timezone.now()
        update_fields.append("started_at")
    game.save(update_fields=update_fields)
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

    if game.borne_off.get(color, 0) >= game.checker_count:
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
    color = game.color_for(user)
    if not color:
        raise GameError("Вы не участвуете в этой игре.")
    if has_blocking_event(game.board, color):
        raise GameError(
            "Нельзя завершить ход: разбейте блок из 6 пунктов без шашки соперника впереди."
        )
    if legal_moves(game, user):
        raise GameError("У вас еще есть допустимые ходы.")

    switch_turn(game)
    game.save()

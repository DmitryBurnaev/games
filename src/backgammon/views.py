from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .app_settings import (
    backgammon_animations_enabled,
    backgammon_debug_tools,
    backgammon_poll_interval_ms,
)
from .models import Game, GameMove, PlayerStats
from .services import (
    GameError,
    apply_move,
    arrange_blocking_event_test,
    arrange_checkers_for_victory_test,
    arrange_extra_head_move_test,
    arrange_checkers_in_home,
    create_roll,
    finish_blocked_turn,
    roll_die,
    serialize_game,
    surrender_game,
    undo_last_move,
)

MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def moscow_datetime_label(value: Any) -> str:
    """Format an aware datetime for the lobby in Moscow time."""
    return timezone.localtime(value, MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")


def effective_game_started_at(game: Game) -> Any:
    """Return the real game start time, falling back for older rows."""
    return game.started_at or game.created_at


def game_duration_label(duration: timedelta) -> str:
    """Return a compact Russian duration label."""
    total_minutes = max(int(duration.total_seconds() // 60), 0)
    days, day_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(day_minutes, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        hour_word = "час" if hours == 1 else "часа" if 2 <= hours <= 4 else "часов"
        parts.append(f"{hours} {hour_word}")
    if minutes or not parts:
        parts.append(f"{minutes} мин")
    return " ".join(parts)


def effective_game_finished_at(game: Game, now: Any) -> Any:
    """Return the duration end point for the lobby."""
    if game.status == Game.Status.FINISHED and game.finished_at:
        return game.finished_at
    return now


def decorate_lobby_game(game: Game, user: Any) -> Game:
    """Attach display-only lobby fields to a game instance."""
    started_at = effective_game_started_at(game)
    ended_at = effective_game_finished_at(game, timezone.now())
    game.started_at_label = moscow_datetime_label(started_at)
    game.duration_label = game_duration_label(ended_at - started_at)
    game.winner_is_viewer = bool(game.winner_id and game.winner_id == user.id)
    return game


def signup(request: HttpRequest) -> HttpResponse:
    """Register a new user through Django's standard auth form."""
    if not request.user.is_anonymous:
        return redirect("backgammon:game_list")
    if not settings.ALLOW_USER_REGISTRATION:
        messages.warning(request, "Регистрация сейчас закрыта.")
        return redirect("login")

    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("backgammon:game_list")
    return render(request, "registration/signup.html", {"form": form})


@login_required
def game_list(request: HttpRequest) -> HttpResponse:
    """Render the lobby with the user's games, open games, and stats."""
    games = Game.objects.select_related(
        "white_player", "black_player", "current_player", "winner"
    )
    my_games = [
        decorate_lobby_game(game, request.user)
        for game in games.filter(
            Q(white_player=request.user) | Q(black_player=request.user)
        )[:20]
    ]
    open_games = [
        decorate_lobby_game(game, request.user)
        for game in games.filter(status=Game.Status.WAITING).exclude(
            white_player=request.user
        )[:20]
    ]
    stats = PlayerStats.objects.filter(user=request.user).first()
    return render(
        request,
        "backgammon/game_list.html",
        {"my_games": my_games, "open_games": open_games, "stats": stats},
    )


@login_required
@require_POST
def create_game(request: HttpRequest) -> HttpResponse:
    """Create a waiting game with the current user as white."""
    game = Game.objects.create(white_player=request.user)
    messages.success(request, "Игра создана. Теперь нужен второй игрок.")
    return redirect("backgammon:game_detail", pk=game.pk)


def can_view_game(game: Game, user: Any) -> bool:
    """Return whether a user may open a game detail page."""
    return game.status == Game.Status.WAITING or bool(game.color_for(user))


@login_required
def game_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Render the playable board for a game."""
    game = get_object_or_404(
        Game.objects.select_related(
            "white_player", "black_player", "current_player", "winner"
        ),
        pk=pk,
    )
    if not can_view_game(game, request.user):
        messages.error(request, "Эта игра доступна только участникам.")
        return redirect("backgammon:game_list")
    return render(
        request,
        "backgammon/game_detail.html",
        {
            "game": game,
            "debug_game_tools": backgammon_debug_tools(),
            "animations_enabled": backgammon_animations_enabled(),
            "poll_interval_ms": backgammon_poll_interval_ms(),
        },
    )


@login_required
@require_POST
def join_game(request: HttpRequest, pk: int) -> HttpResponse:
    """Seat the current user as black and choose the starting player."""
    with transaction.atomic():
        game = get_object_or_404(Game.objects.select_for_update(), pk=pk)
        if game.status != Game.Status.WAITING or game.black_player:
            messages.error(request, "К этой игре уже нельзя присоединиться.")
            return redirect("backgammon:game_detail", pk=game.pk)
        if game.white_player_id == request.user.id:
            messages.error(request, "Нельзя играть против себя.")
            return redirect("backgammon:game_detail", pk=game.pk)

        white_die = black_die = 0
        while white_die == black_die:
            white_die = roll_die()
            black_die = roll_die()
        game.black_player = request.user
        game.current_player = (
            game.white_player if white_die > black_die else request.user
        )
        game.status = Game.Status.ACTIVE
        game.save(
            update_fields=["black_player", "current_player", "status", "updated_at"]
        )
        GameMove.objects.create(
            game=game,
            player=request.user,
            action=GameMove.Action.JOIN,
            dice=[white_die, black_die],
            board=game.board,
        )

    messages.success(request, "Вы присоединились к игре.")
    return redirect("backgammon:game_detail", pk=game.pk)


def json_error(message: str, status: int = 400) -> JsonResponse:
    """Return the standard JSON error shape used by game endpoints."""
    return JsonResponse({"ok": False, "error": message}, status=status)


def get_json_body(request: HttpRequest) -> dict[str, Any]:
    """Decode a JSON request body into a dictionary."""
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        raise GameError("Некорректный JSON.")


def get_participant_game(pk: int, user: Any, for_update: bool = False) -> Game:
    """Fetch a game and ensure the user is one of its players."""
    queryset = Game.objects.select_related(
        "white_player", "black_player", "current_player", "winner"
    )
    if for_update:
        queryset = queryset.select_for_update()
    game = get_object_or_404(queryset, pk=pk)
    if not game.color_for(user):
        raise GameError("Вы не участвуете в этой игре.")
    return game


@login_required
def game_state(request: HttpRequest, pk: int) -> JsonResponse:
    """Return the current serialized game state for polling."""
    game = get_object_or_404(
        Game.objects.select_related(
            "white_player", "black_player", "current_player", "winner"
        ),
        pk=pk,
    )
    if not can_view_game(game, request.user):
        return json_error("Эта игра доступна только участникам.", status=403)
    return JsonResponse({"ok": True, "game": serialize_game(game, request.user)})


@login_required
@require_POST
def roll(request: HttpRequest, pk: int) -> JsonResponse:
    """Roll dice for the current player."""
    try:
        with transaction.atomic():
            game = get_participant_game(pk, request.user, for_update=True)
            create_roll(game, request.user)
            game.refresh_from_db()
            payload = serialize_game(game, request.user)
    except GameError as exc:
        return json_error(str(exc))
    return JsonResponse({"ok": True, "game": payload})


@login_required
@require_POST
def move(request: HttpRequest, pk: int) -> JsonResponse:
    """Apply one checker move chosen on the board."""
    try:
        data = get_json_body(request)
        source_point = int(data.get("source"))
        distance = int(data.get("distance"))
        with transaction.atomic():
            game = get_participant_game(pk, request.user, for_update=True)
            apply_move(game, request.user, source_point, distance)
            game.refresh_from_db()
            payload = serialize_game(game, request.user)
    except TypeError, ValueError:
        return json_error("Некорректные параметры хода.")
    except GameError as exc:
        return json_error(str(exc))
    return JsonResponse({"ok": True, "game": payload})


@login_required
@require_POST
def surrender(request: HttpRequest, pk: int) -> JsonResponse:
    """Finish the game by resigning and giving victory to the opponent."""
    try:
        data = get_json_body(request)
        with transaction.atomic():
            game = get_participant_game(pk, request.user, for_update=True)
            surrender_game(game, request.user, data.get("victory_type"))
            game.refresh_from_db()
            payload = serialize_game(game, request.user)
    except GameError as exc:
        return json_error(str(exc))
    return JsonResponse({"ok": True, "game": payload})


@login_required
@require_POST
def prepare_bear_off(request: HttpRequest, pk: int) -> JsonResponse:
    """Move the current user's checkers into home for finish testing."""
    if not backgammon_debug_tools():
        return json_error("Отладочные игровые инструменты выключены.", status=403)
    try:
        with transaction.atomic():
            game = get_participant_game(pk, request.user, for_update=True)
            arrange_checkers_in_home(game, request.user)
            game.refresh_from_db()
            payload = serialize_game(game, request.user)
    except GameError as exc:
        return json_error(str(exc))
    return JsonResponse({"ok": True, "game": payload})


@login_required
@require_POST
def prepare_victory(request: HttpRequest, pk: int) -> JsonResponse:
    """Prepare a near-finished board for testing victory animation."""
    if not backgammon_debug_tools():
        return json_error("Отладочные игровые инструменты выключены.", status=403)
    try:
        with transaction.atomic():
            game = get_participant_game(pk, request.user, for_update=True)
            arrange_checkers_for_victory_test(game, request.user)
            game.refresh_from_db()
            payload = serialize_game(game, request.user)
    except GameError as exc:
        return json_error(str(exc))
    return JsonResponse({"ok": True, "game": payload})


@login_required
@require_POST
def prepare_extra_head_move(request: HttpRequest, pk: int) -> JsonResponse:
    """Prepare a first-turn blocked-head position for testing."""
    if not backgammon_debug_tools():
        return json_error("Отладочные игровые инструменты выключены.", status=403)
    try:
        with transaction.atomic():
            game = get_participant_game(pk, request.user, for_update=True)
            arrange_extra_head_move_test(game, request.user)
            game.refresh_from_db()
            payload = serialize_game(game, request.user)
    except GameError as exc:
        return json_error(str(exc))
    return JsonResponse({"ok": True, "game": payload})


@login_required
@require_POST
def prepare_blocking_event(request: HttpRequest, pk: int) -> JsonResponse:
    """Prepare a six-block state that blocks turn finishing."""
    if not backgammon_debug_tools():
        return json_error("Отладочные игровые инструменты выключены.", status=403)
    try:
        with transaction.atomic():
            game = get_participant_game(pk, request.user, for_update=True)
            arrange_blocking_event_test(game, request.user)
            game.refresh_from_db()
            payload = serialize_game(game, request.user)
    except GameError as exc:
        return json_error(str(exc))
    return JsonResponse({"ok": True, "game": payload})


@login_required
@require_POST
def undo_move(request: HttpRequest, pk: int) -> JsonResponse:
    """Undo the current player's latest checker move."""
    try:
        with transaction.atomic():
            game = get_participant_game(pk, request.user, for_update=True)
            undo_last_move(game, request.user)
            game.refresh_from_db()
            payload = serialize_game(game, request.user)
    except GameError as exc:
        return json_error(str(exc))
    return JsonResponse({"ok": True, "game": payload})


@login_required
@require_POST
def end_turn(request: HttpRequest, pk: int) -> JsonResponse:
    """Explicitly finish a turn after all legal moves are exhausted."""
    try:
        with transaction.atomic():
            game = get_participant_game(pk, request.user, for_update=True)
            finish_blocked_turn(game, request.user)
            game.refresh_from_db()
            payload = serialize_game(game, request.user)
    except GameError as exc:
        return json_error(str(exc))
    return JsonResponse({"ok": True, "game": payload})

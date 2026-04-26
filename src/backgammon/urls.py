from django.urls import path

from . import views

app_name = "backgammon"

urlpatterns = [
    path("", views.game_list, name="game_list"),
    path("signup/", views.signup, name="signup"),
    path("games/new/", views.create_game, name="create_game"),
    path("games/<int:pk>/", views.game_detail, name="game_detail"),
    path("games/<int:pk>/join/", views.join_game, name="join_game"),
    path("games/<int:pk>/state/", views.game_state, name="game_state"),
    path("games/<int:pk>/roll/", views.roll, name="roll"),
    path("games/<int:pk>/move/", views.move, name="move"),
    path(
        "games/<int:pk>/prepare-bear-off/",
        views.prepare_bear_off,
        name="prepare_bear_off",
    ),
    path(
        "games/<int:pk>/prepare-victory/",
        views.prepare_victory,
        name="prepare_victory",
    ),
    path("games/<int:pk>/undo/", views.undo_move, name="undo_move"),
    path("games/<int:pk>/end-turn/", views.end_turn, name="end_turn"),
]

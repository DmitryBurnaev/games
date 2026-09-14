from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Game


class FinishedGameArchiveTests(TestCase):
    """Coverage for the participant-only finished-game archive."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.white = user_model.objects.create_user(username="white")
        self.black = user_model.objects.create_user(username="black")
        self.client.force_login(self.white)

    def finished_game(self, number: int) -> Game:
        game = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            winner=self.white,
            status=Game.Status.FINISHED,
            party_number=number,
            finished_at=timezone.now() - timedelta(minutes=number),
        )
        return game

    def test_lobby_keeps_finished_games_and_links_to_archive(self) -> None:
        finished = self.finished_game(1)
        active = Game.objects.create(
            white_player=self.white,
            black_player=self.black,
            current_player=self.white,
            status=Game.Status.ACTIVE,
            party_number=2,
        )

        response = self.client.get(reverse("backgammon:game_list"))

        self.assertContains(response, "Мои игры")
        self.assertContains(response, f"#{active.party_number}")
        self.assertContains(response, f"#{finished.party_number}")
        self.assertContains(response, reverse("backgammon:finished_game_archive"))

    def test_archive_is_participant_only_and_paginated(self) -> None:
        games = [self.finished_game(number) for number in range(1, 22)]

        first_page = self.client.get(reverse("backgammon:finished_game_archive"))
        second_page = self.client.get(
            reverse("backgammon:finished_game_archive"), {"page": 2}
        )

        self.assertContains(first_page, "Архив игр")
        self.assertContains(first_page, f"#{games[0].party_number}")
        self.assertNotContains(first_page, f"#{games[-1].party_number}")
        self.assertContains(second_page, f"#{games[-1].party_number}")

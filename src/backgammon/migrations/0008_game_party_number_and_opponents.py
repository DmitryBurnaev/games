import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def game_player_pair(game):
    player_ids = [game.white_player_id, game.black_player_id]
    player_ids = [player_id for player_id in player_ids if player_id is not None]
    if len(player_ids) == 1 and game.planned_opponent_id is not None:
        player_ids.append(game.planned_opponent_id)
    if len(player_ids) != 2 or len(set(player_ids)) != 2:
        return None
    return tuple(sorted(player_ids))


def backfill_party_numbers(apps, schema_editor):
    Game = apps.get_model("backgammon", "Game")
    next_numbers = {}

    for game in Game.objects.order_by("created_at", "pk").iterator():
        pair = game_player_pair(game)
        if pair is None:
            continue
        party_number = next_numbers.get(pair, 0) + 1
        Game.objects.filter(pk=game.pk).update(party_number=party_number)
        next_numbers[pair] = party_number


class Migration(migrations.Migration):

    dependencies = [
        ("backgammon", "0007_game_setup_options"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="game",
            name="party_number",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
        migrations.AddField(
            model_name="game",
            name="planned_opponent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="planned_backgammon_games",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="backgammonplayerpreference",
            name="default_opponent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="preferred_by_backgammon_players",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(backfill_party_numbers, migrations.RunPython.noop),
    ]

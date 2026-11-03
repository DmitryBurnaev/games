from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("backgammon", "0012_game_finished_statistics")]

    operations = [
        migrations.AddField(
            model_name="game",
            name="doubles_only",
            field=models.BooleanField(default=False, editable=False),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("backgammon", "0011_gamepositionset")]

    operations = [
        migrations.AddField(
            model_name="game",
            name="finished_dice_statistics",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="game",
            name="finished_skipped_statistics",
            field=models.JSONField(blank=True, null=True),
        ),
    ]

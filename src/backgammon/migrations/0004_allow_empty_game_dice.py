from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("backgammon", "0003_appsetting"),
    ]

    operations = [
        migrations.AlterField(
            model_name="game",
            name="dice",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterField(
            model_name="game",
            name="remaining_moves",
            field=models.JSONField(blank=True, default=list),
        ),
    ]

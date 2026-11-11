from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("backgammon", "0014_doubles_selection_setting")]

    operations = [
        migrations.AddField(
            model_name="game",
            name="exclude_from_statistics",
            field=models.BooleanField(
                verbose_name="Не учитывать в статистике",
                default=False,
                help_text="Исключает партию из общего игрового времени и средней длительности.",
            ),
        ),
    ]

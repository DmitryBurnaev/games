from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("backgammon", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="game",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

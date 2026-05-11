from django.conf import settings
from django.db import migrations, models


def seed_app_settings(apps, schema_editor):
    AppSetting = apps.get_model("backgammon", "AppSetting")
    initial_settings = {
        "BACKGAMMON_DEBUG_TOOLS": str(settings.BACKGAMMON_DEBUG_TOOLS).lower(),
        "BACKGAMMON_DICE_MODE": settings.BACKGAMMON_DICE_MODE,
        "BACKGAMMON_ANIMATIONS_ENABLED": str(
            settings.BACKGAMMON_ANIMATIONS_ENABLED
        ).lower(),
        "BACKGAMMON_POLL_INTERVAL_MS": str(settings.BACKGAMMON_POLL_INTERVAL_MS),
    }
    for key, value in initial_settings.items():
        AppSetting.objects.get_or_create(
            key=key,
            defaults={"value": value, "is_enabled": False},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("backgammon", "0002_game_started_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="AppSetting",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("key", models.CharField(max_length=96, unique=True)),
                ("value", models.TextField(blank=True)),
                ("is_enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["key"],
            },
        ),
        migrations.RunPython(seed_app_settings, migrations.RunPython.noop),
    ]

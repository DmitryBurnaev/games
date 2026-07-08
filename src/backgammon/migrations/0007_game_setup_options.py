import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def seed_checker_count_setting(apps, schema_editor):
    AppSetting = apps.get_model("backgammon", "AppSetting")
    value = ",".join(str(item) for item in settings.BACKGAMMON_CHECKER_COUNT_PRESETS)
    AppSetting.objects.get_or_create(
        key="BACKGAMMON_CHECKER_COUNT_PRESETS",
        defaults={
            "value": value,
            "is_enabled": False,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("backgammon", "0006_quicknotificationpreset"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="game",
            name="white_player",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="white_backgammon_games",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="game",
            name="checker_count",
            field=models.PositiveSmallIntegerField(default=15),
        ),
        migrations.CreateModel(
            name="BackgammonPlayerPreference",
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
                (
                    "default_checker_color",
                    models.CharField(
                        choices=[("white", "White"), ("black", "Black")],
                        default="white",
                        max_length=8,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="backgammon_preferences",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["user__username"],
            },
        ),
        migrations.RunPython(
            seed_checker_count_setting,
            migrations.RunPython.noop,
        ),
    ]

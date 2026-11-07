from django.db import migrations


def seed_doubles_selection_setting(apps, schema_editor):
    AppSetting = apps.get_model("backgammon", "AppSetting")
    AppSetting.objects.using(schema_editor.connection.alias).get_or_create(
        key="BACKGAMMON_DOUBLES_SELECTION_MODE",
        defaults={"value": "FIRST", "is_enabled": False},
    )


class Migration(migrations.Migration):

    dependencies = [("backgammon", "0013_game_doubles_only")]

    operations = [
        migrations.RunPython(seed_doubles_selection_setting, migrations.RunPython.noop),
    ]

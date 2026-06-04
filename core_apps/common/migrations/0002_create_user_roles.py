# Generated manually on 2026-06-03

from django.db import migrations


def create_user_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")

    for name in ("Administrador", "Operador"):
        Group.objects.get_or_create(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("common", "0001_usersetting"),
    ]

    operations = [
        migrations.RunPython(create_user_roles, migrations.RunPython.noop),
    ]

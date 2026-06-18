# Generated manually for role-based access.

from django.db import migrations


def create_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")

    for name in ("Jefe", "Operador"):
        Group.objects.get_or_create(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0002_create_user_roles"),
    ]

    operations = [
        migrations.RunPython(create_roles, migrations.RunPython.noop),
    ]

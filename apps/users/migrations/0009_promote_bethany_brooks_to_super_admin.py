from django.db import migrations
from django.db.models import Q

ADMIN_FIRST_NAMES = ("bethany", "brook", "brooks")


def promote(apps, schema_editor):
    """Give Bethany's and Brooks's existing CRM accounts the Super Admin role."""
    CustomUser = apps.get_model("users", "CustomUser")
    name_filter = Q()
    for name in ADMIN_FIRST_NAMES:
        name_filter |= Q(first_name__iexact=name) | Q(email__istartswith=f"{name}@") | Q(username__iexact=name)
    CustomUser.objects.filter(name_filter, is_active=True, is_deleted=False).filter(
        Q(role__in=["crm_user", "super_admin"]) | Q(is_staff=True) | Q(is_superuser=True)
    ).update(role="super_admin", is_staff=True, hiring_enabled=True)


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0008_alter_profile_timezone"),
    ]

    operations = [migrations.RunPython(promote, migrations.RunPython.noop)]

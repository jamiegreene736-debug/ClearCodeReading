from django.db.models import Q

from apps.users.models import CustomUser


def crm_owner_queryset():
    return CustomUser.objects.filter(is_active=True, is_deleted=False).filter(
        Q(is_superuser=True)
        | Q(is_staff=True)
        | Q(role__in=[CustomUser.Role.SUPER_ADMIN, CustomUser.Role.CRM_USER])
    ).distinct().order_by("first_name", "last_name", "email")

from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import user_can_log_session
from apps.schools.models import SchoolMembership
from apps.users.models import AuditLog, ChildProfile, CustomUser, GuardianRelationship, MobileDevice


class MobileDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MobileDevice
        fields = [
            "device_id",
            "push_token",
            "environment",
            "app_version",
            "is_active",
            "last_seen_at",
        ]
        read_only_fields = ["is_active", "last_seen_at"]


class MobileBootstrapView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        memberships = list(
            SchoolMembership.objects.filter(user=user, is_deleted=False)
            .select_related("school")
            .order_by("school__name")
        )
        children = list(_visible_children(user))
        return Response(
            {
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "display_name": user.get_full_name() or user.email,
                    "role": user.role,
                },
                "memberships": [
                    {
                        "id": membership.id,
                        "center_id": membership.school_id,
                        "center_name": membership.school.name,
                        "center_slug": membership.school.slug,
                        "role": membership.role,
                        "title": membership.title,
                        "permissions": membership.permissions,
                    }
                    for membership in memberships
                ],
                "children": [
                    {
                        "id": child.id,
                        "first_name": child.first_name,
                        "display_name": str(child),
                        "grade_level": child.grade_level,
                        "center_id": child.school_id,
                        "center_name": child.school.name if child.school else "",
                        "idea_services_authorized": child.idea_services_authorized,
                    }
                    for child in children
                ],
                "capabilities": _capabilities(user, memberships, children),
                "generated_at": timezone.now(),
            }
        )


class MobileDeviceView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = MobileDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        device, _ = MobileDevice.objects.update_or_create(
            user=request.user,
            device_id=serializer.validated_data["device_id"],
            defaults={
                "push_token": serializer.validated_data.get("push_token", ""),
                "environment": serializer.validated_data.get(
                    "environment",
                    MobileDevice.Environment.SANDBOX,
                ),
                "app_version": serializer.validated_data.get("app_version", ""),
                "is_active": True,
                "last_seen_at": timezone.now(),
            },
        )
        return Response(MobileDeviceSerializer(device).data)

    def delete(self, request):
        device_id = request.data.get("device_id")
        if not device_id:
            return Response({"device_id": "This field is required."}, status=status.HTTP_400_BAD_REQUEST)
        updated = MobileDevice.objects.filter(
            user=request.user,
            device_id=device_id,
            is_active=True,
        ).update(is_active=False, last_seen_at=timezone.now())
        return Response({"deactivated": updated})


class MobileLogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        device_id = request.data.get("device_id")
        devices = MobileDevice.objects.filter(user=request.user, is_active=True)
        if device_id:
            devices = devices.filter(device_id=device_id)
        deactivated = devices.update(is_active=False, last_seen_at=timezone.now())
        AuditLog.objects.create(
            actor=request.user,
            action="mobile.logout",
            entity_type="users.CustomUser",
            entity_id=str(request.user.id),
            after={"devices_deactivated": deactivated},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


def _visible_children(user):
    queryset = ChildProfile.objects.filter(is_deleted=False).select_related("school")
    if user.is_superuser or user.role == CustomUser.Role.SUPER_ADMIN:
        return queryset.order_by("last_name", "first_name")
    if user.role == CustomUser.Role.GUARDIAN:
        return (
            queryset.filter(
                guardian_relationships__guardian=user,
                guardian_relationships__is_deleted=False,
                guardian_relationships__consent_status=GuardianRelationship.ConsentStatus.GRANTED,
            )
            .filter(
                Q(guardian_relationships__consent_expires_at__isnull=True)
                | Q(guardian_relationships__consent_expires_at__gt=timezone.now())
            )
            .distinct()
            .order_by("last_name", "first_name")
        )
    if user.role == CustomUser.Role.STUDENT:
        return queryset.filter(user=user)
    return (
        queryset.filter(
            school__memberships__user=user,
            school__memberships__is_deleted=False,
        )
        .distinct()
        .order_by("last_name", "first_name")
    )


def _capabilities(user, memberships, children):
    leadership = (
        user.is_superuser
        or user.role in {CustomUser.Role.SUPER_ADMIN, CustomUser.Role.SCHOOL_ADMIN}
        or any(
            membership.role in {SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN}
            for membership in memberships
        )
    )
    return {
        "log_sessions": any(user_can_log_session(user, child) for child in children),
        "view_progress": bool(children),
        "manage_schedules": leadership,
        "view_outcomes": leadership
        or any(membership.permissions.get("outcomes_reports") is True for membership in memberships),
        "manage_consents": leadership,
    }

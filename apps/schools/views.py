from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import IsSchoolAdmin
from apps.schools.models import School, SchoolMembership
from apps.schools.serializers import SchoolMembershipSerializer, SchoolSerializer
from apps.users.models import AuditLog, CustomUser
from apps.users.serializers import CustomUserSerializer


class SchoolOnboardingSerializer(serializers.Serializer):
    school = serializers.DictField()
    admin = serializers.DictField()


class SchoolViewSet(viewsets.ModelViewSet):
    serializer_class = SchoolSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = School.objects.filter(is_deleted=False).prefetch_related("memberships")
        user = self.request.user
        if not user.is_superuser and getattr(user, "role", None) != CustomUser.Role.SUPER_ADMIN:
            queryset = queryset.filter(memberships__user=user, memberships__is_deleted=False)
        return queryset.distinct()

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated, IsSchoolAdmin])
    def onboard(self, request):
        serializer = SchoolOnboardingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        school_data = serializer.validated_data["school"]
        admin_data = serializer.validated_data["admin"]

        with transaction.atomic():
            school = School.objects.create(
                schema_name=school_data["schema_name"],
                name=school_data["name"],
                slug=school_data["slug"],
                district=school_data.get("district", ""),
                address=school_data.get("address", {}),
                contact_email=school_data.get("contact_email", ""),
                contact_phone=school_data.get("contact_phone", ""),
                settings=school_data.get("settings", {}),
                branding=school_data.get("branding", {}),
                paid_until=school_data.get("paid_until"),
                on_trial=school_data.get("on_trial", True),
            )
            password = admin_data.pop("password", None)
            admin_user, created = CustomUser.objects.get_or_create(
                email=admin_data["email"],
                defaults={
                    "username": admin_data.get("username", admin_data["email"]),
                    "first_name": admin_data.get("first_name", ""),
                    "last_name": admin_data.get("last_name", ""),
                    "role": CustomUser.Role.SCHOOL_ADMIN,
                    "phone_number": admin_data.get("phone_number", ""),
                },
            )
            if created and password:
                admin_user.set_password(password)
                admin_user.save(update_fields=["password"])

            membership = SchoolMembership.objects.create(
                school=school,
                user=admin_user,
                role=SchoolMembership.Role.OWNER,
                title=admin_data.get("title", "School Administrator"),
                invited_by=request.user,
                joined_at=timezone.now(),
            )
            AuditLog.objects.create(
                actor=request.user,
                action="school.onboarded",
                entity_type="School",
                entity_id=str(school.id),
                after={"admin_user_id": admin_user.id, "membership_id": membership.id},
            )

        return Response(
            {
                "school": SchoolSerializer(school, context=self.get_serializer_context()).data,
                "admin": CustomUserSerializer(admin_user, context=self.get_serializer_context()).data,
                "membership": SchoolMembershipSerializer(membership, context=self.get_serializer_context()).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsSchoolAdmin])
    def invite(self, request, pk=None):
        school = self.get_object()
        user_id = request.data.get("user_id")
        role = request.data.get("role", SchoolMembership.Role.TEACHER)
        if not user_id:
            return Response({"user_id": "This field is required."}, status=status.HTTP_400_BAD_REQUEST)
        user = CustomUser.objects.get(id=user_id)
        membership, _ = SchoolMembership.objects.update_or_create(
            school=school,
            user=user,
            defaults={
                "role": role,
                "title": request.data.get("title", ""),
                "permissions": request.data.get("permissions", {}),
                "invited_by": request.user,
            },
        )
        return Response(SchoolMembershipSerializer(membership, context=self.get_serializer_context()).data)


class SchoolMembershipViewSet(viewsets.ModelViewSet):
    serializer_class = SchoolMembershipSerializer
    permission_classes = [IsAuthenticated, IsSchoolAdmin]

    def get_queryset(self):
        return SchoolMembership.objects.filter(is_deleted=False).select_related("school", "user", "invited_by")

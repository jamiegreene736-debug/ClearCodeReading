from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.users.models import AuditLog, ChildProfile, ConsentLog, CustomUser, GuardianRelationship, Profile
from apps.users.serializers import (
    AuditLogSerializer,
    ChildProfileSerializer,
    ConsentLogSerializer,
    CustomUserSerializer,
    GuardianRelationshipSerializer,
    ProfileSerializer,
)


class ParentChildRegistrationSerializer(serializers.Serializer):
    parent = serializers.DictField()
    child = serializers.DictField()
    relationship_type = serializers.ChoiceField(choices=GuardianRelationship.RelationshipType.choices)
    consents = serializers.ListField(child=serializers.DictField(), allow_empty=False)


class UserViewSet(viewsets.ModelViewSet):
    queryset = CustomUser.objects.filter(is_deleted=False).select_related("profile")
    serializer_class = CustomUserSerializer

    def get_permissions(self):
        if self.action == "register_parent_child":
            return [AllowAny()]
        return [IsAuthenticated()]

    @action(detail=False, methods=["post"], url_path="register-parent-child")
    def register_parent_child(self, request):
        serializer = ParentChildRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parent_data = serializer.validated_data["parent"]
        child_data = serializer.validated_data["child"]

        with transaction.atomic():
            password = parent_data.pop("password", None)
            parent, created = CustomUser.objects.get_or_create(
                email=parent_data["email"],
                defaults={
                    "username": parent_data.get("username", parent_data["email"]),
                    "first_name": parent_data.get("first_name", ""),
                    "last_name": parent_data.get("last_name", ""),
                    "role": CustomUser.Role.GUARDIAN,
                    "phone_number": parent_data.get("phone_number", ""),
                },
            )
            if created and password:
                parent.set_password(password)
                parent.save(update_fields=["password"])

            child = ChildProfile.objects.create(
                first_name=child_data["first_name"],
                last_name=child_data.get("last_name", ""),
                date_of_birth=child_data.get("date_of_birth"),
                grade_level=child_data.get("grade_level", ""),
                school_id=child_data.get("school_id"),
                learning_profile=child_data.get("learning_profile", {}),
                accommodations=child_data.get("accommodations", []),
            )
            relationship = GuardianRelationship.objects.create(
                guardian=parent,
                child=child,
                relationship_type=serializer.validated_data["relationship_type"],
                is_primary=True,
                consent_status=GuardianRelationship.ConsentStatus.PENDING,
            )

            consent_logs = []
            for consent in serializer.validated_data["consents"]:
                consent_logs.append(
                    ConsentLog.objects.create(
                        guardian_relationship=relationship,
                        guardian=parent,
                        child=child,
                        consent_type=consent["consent_type"],
                        status=consent.get("status", ConsentLog.Status.GRANTED),
                        version=consent.get("version", ""),
                        source=consent.get("source", "registration"),
                        ip_address=request.META.get("REMOTE_ADDR"),
                        user_agent=request.META.get("HTTP_USER_AGENT", ""),
                        expires_at=consent.get("expires_at"),
                        metadata=consent.get("metadata", {}),
                    )
                )

            AuditLog.objects.create(
                actor=parent,
                action="parent_child.registration",
                entity_type="ChildProfile",
                entity_id=str(child.id),
                after={"relationship_id": relationship.id, "consent_log_ids": [log.id for log in consent_logs]},
            )

        return Response(
            {
                "parent": CustomUserSerializer(parent, context=self.get_serializer_context()).data,
                "child": ChildProfileSerializer(child, context=self.get_serializer_context()).data,
                "relationship": GuardianRelationshipSerializer(relationship, context=self.get_serializer_context()).data,
            },
            status=status.HTTP_201_CREATED,
        )


class ProfileViewSet(viewsets.ModelViewSet):
    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Profile.objects.filter(is_deleted=False).select_related("user")


class ChildProfileViewSet(viewsets.ModelViewSet):
    serializer_class = ChildProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = ChildProfile.objects.filter(is_deleted=False).select_related("user", "school")
        if getattr(self.request.user, "role", None) == CustomUser.Role.GUARDIAN:
            queryset = queryset.filter(guardian_relationships__guardian=self.request.user)
        return queryset.distinct()


class GuardianRelationshipViewSet(viewsets.ModelViewSet):
    serializer_class = GuardianRelationshipSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = GuardianRelationship.objects.filter(is_deleted=False).select_related("guardian", "child")
        if getattr(self.request.user, "role", None) == CustomUser.Role.GUARDIAN:
            queryset = queryset.filter(guardian=self.request.user)
        return queryset

    @action(detail=True, methods=["post"], url_path="grant-consent")
    def grant_consent(self, request, pk=None):
        relationship = self.get_object()
        consent_type = request.data.get("consent_type", ConsentLog.ConsentType.DATA_PROCESSING)
        log = ConsentLog.objects.create(
            guardian_relationship=relationship,
            guardian=relationship.guardian,
            child=relationship.child,
            consent_type=consent_type,
            status=ConsentLog.Status.GRANTED,
            version=request.data.get("version", ""),
            source=request.data.get("source", "api"),
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            expires_at=request.data.get("expires_at") or None,
            metadata=request.data.get("metadata", {}),
        )
        relationship.consent_status = GuardianRelationship.ConsentStatus.GRANTED
        relationship.consent_expires_at = log.expires_at
        relationship.save(update_fields=["consent_status", "consent_expires_at", "updated_at"])
        return Response(ConsentLogSerializer(log, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="revoke-consent")
    def revoke_consent(self, request, pk=None):
        relationship = self.get_object()
        log = ConsentLog.objects.create(
            guardian_relationship=relationship,
            guardian=relationship.guardian,
            child=relationship.child,
            consent_type=request.data.get("consent_type", ConsentLog.ConsentType.DATA_PROCESSING),
            status=ConsentLog.Status.REVOKED,
            source=request.data.get("source", "api"),
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            metadata=request.data.get("metadata", {}),
        )
        relationship.consent_status = GuardianRelationship.ConsentStatus.REVOKED
        relationship.consent_expires_at = timezone.now()
        relationship.save(update_fields=["consent_status", "consent_expires_at", "updated_at"])
        return Response(ConsentLogSerializer(log, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)


class ConsentLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ConsentLogSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = ConsentLog.objects.select_related("guardian_relationship", "guardian", "child")
        if getattr(self.request.user, "role", None) == CustomUser.Role.GUARDIAN:
            queryset = queryset.filter(guardian=self.request.user)
        return queryset


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated]
    queryset = AuditLog.objects.select_related("actor")

# Clear Code Reading ViewSets and API Workflow Logic

## `apps/assessments/views.py`
```python
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import COPPAConsentRequired, IsEvaluator, has_coppa_consent, user_can_evaluate_child
from apps.assessments.models import Assessment
from apps.assessments.serializers import AssessmentSerializer
from apps.assessments.tasks import notify_assessment_review_completed
from apps.progress.models import Progress
from apps.users.models import AuditLog


class DigitalAssessmentSubmissionSerializer(serializers.Serializer):
    responses = serializers.JSONField()
    raw_score = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    max_score = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    scoring = serializers.JSONField(required=False)
    recommendations = serializers.JSONField(required=False)
    metadata = serializers.JSONField(required=False)


class HumanReviewSerializer(serializers.Serializer):
    raw_score = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    max_score = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    percentile = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    scoring = serializers.JSONField(required=False)
    recommendations = serializers.JSONField(required=False)
    reviewer_notes = serializers.CharField(required=False, allow_blank=True)


class StatusTransitionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Assessment.Status.choices)


class AssessmentViewSet(viewsets.ModelViewSet):
    serializer_class = AssessmentSerializer
    permission_classes = [IsAuthenticated, COPPAConsentRequired]

    def get_queryset(self):
        queryset = Assessment.objects.select_related("child", "school", "assigned_by", "skill").filter(is_deleted=False)
        user = self.request.user
        if getattr(user, "role", None) == user.Role.GUARDIAN:
            queryset = queryset.filter(child__guardian_relationships__guardian=user)
        elif not user.is_superuser and getattr(user, "role", None) not in {user.Role.SUPER_ADMIN, user.Role.SCHOOL_ADMIN, user.Role.TEACHER}:
            queryset = queryset.none()

        child_id = self.request.query_params.get("child")
        status_value = self.request.query_params.get("status")
        if child_id:
            queryset = queryset.filter(child_id=child_id)
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset.distinct()

    def perform_create(self, serializer):
        child = serializer.validated_data["child"]
        if not has_coppa_consent(child):
            raise serializers.ValidationError({"child": "COPPA consent is required before assigning assessments."})
        serializer.save(assigned_by=self.request.user, status=Assessment.Status.PENDING)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, COPPAConsentRequired])
    def submit(self, request, pk=None):
        assessment = self.get_object()
        if assessment.status not in {Assessment.Status.PENDING, Assessment.Status.IN_PROGRESS}:
            return Response(
                {"detail": "Only pending or in-progress assessments can be submitted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = DigitalAssessmentSubmissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            assessment.responses = serializer.validated_data["responses"]
            assessment.raw_score = serializer.validated_data.get("raw_score", assessment.raw_score)
            assessment.max_score = serializer.validated_data.get("max_score", assessment.max_score)
            assessment.scoring = serializer.validated_data.get("scoring", assessment.scoring)
            assessment.recommendations = serializer.validated_data.get("recommendations", assessment.recommendations)
            assessment.metadata.update(serializer.validated_data.get("metadata", {}))
            assessment.started_at = assessment.started_at or timezone.now()
            assessment.completed_at = timezone.now()
            assessment.status = Assessment.Status.HUMAN_REVIEW
            assessment.save()

            AuditLog.objects.create(
                actor=request.user,
                action="assessment.submitted",
                entity_type="Assessment",
                entity_id=str(assessment.id),
                after={"status": assessment.status, "child_id": assessment.child_id},
            )

        return Response(AssessmentSerializer(assessment, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsEvaluator])
    def review(self, request, pk=None):
        assessment = self.get_object()
        if not user_can_evaluate_child(request.user, assessment.child):
            return Response({"detail": "You are not assigned to evaluate this child."}, status=status.HTTP_403_FORBIDDEN)
        if assessment.status != Assessment.Status.HUMAN_REVIEW:
            return Response(
                {"detail": "Only assessments in human review can be completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = HumanReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            for field in ["raw_score", "max_score", "percentile", "scoring", "recommendations"]:
                if field in serializer.validated_data:
                    setattr(assessment, field, serializer.validated_data[field])
            if serializer.validated_data.get("reviewer_notes"):
                assessment.metadata["reviewer_notes"] = serializer.validated_data["reviewer_notes"]
            assessment.assigned_by = request.user
            assessment.completed_at = timezone.now()
            assessment.status = Assessment.Status.COMPLETED
            assessment.save()

            if assessment.skill_id:
                Progress.objects.update_or_create(
                    child=assessment.child,
                    skill=assessment.skill,
                    defaults={
                        "school": assessment.school or assessment.child.school,
                        "status": Progress.Status.PROFICIENT,
                        "current_score": assessment.raw_score,
                        "last_assessment": assessment,
                        "attempts": 1,
                        "evidence": [{"assessment_id": assessment.id, "reviewed_at": assessment.completed_at.isoformat()}],
                    },
                )

            AuditLog.objects.create(
                actor=request.user,
                action="assessment.review_completed",
                entity_type="Assessment",
                entity_id=str(assessment.id),
                after={"status": assessment.status, "child_id": assessment.child_id},
            )

        notify_assessment_review_completed.delay(assessment.id)
        return Response(AssessmentSerializer(assessment, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated, IsEvaluator])
    def transition(self, request, pk=None):
        assessment = self.get_object()
        serializer = StatusTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        requested_status = serializer.validated_data["status"]

        allowed = {
            Assessment.Status.PENDING: {Assessment.Status.IN_PROGRESS, Assessment.Status.HUMAN_REVIEW, Assessment.Status.ARCHIVED},
            Assessment.Status.IN_PROGRESS: {Assessment.Status.HUMAN_REVIEW, Assessment.Status.ARCHIVED},
            Assessment.Status.HUMAN_REVIEW: {Assessment.Status.COMPLETED, Assessment.Status.ARCHIVED},
            Assessment.Status.COMPLETED: set(),
            Assessment.Status.ARCHIVED: set(),
        }
        if requested_status not in allowed.get(assessment.status, set()):
            return Response(
                {"detail": f"Invalid transition from {assessment.status} to {requested_status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        assessment.status = requested_status
        assessment.save(update_fields=["status", "updated_at"])
        return Response(AssessmentSerializer(assessment, context=self.get_serializer_context()).data)

```

## `apps/users/views.py`
```python
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

```

## `apps/schools/views.py`
```python
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

```

## `apps/curriculum/views.py`
```python
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import has_coppa_consent
from apps.curriculum.models import Lesson, Skill, TeachingAid
from apps.curriculum.serializers import LessonSerializer, SkillSerializer, TeachingAidSerializer
from apps.progress.models import Progress
from apps.users.models import ChildProfile


class SkillViewSet(viewsets.ModelViewSet):
    serializer_class = SkillSerializer
    permission_classes = [IsAuthenticated]
    queryset = Skill.objects.filter(is_deleted=False).prefetch_related("prerequisites")


class LessonViewSet(viewsets.ModelViewSet):
    serializer_class = LessonSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Lesson.objects.filter(is_deleted=False).select_related("skill").prefetch_related("teaching_aids")
        if self.request.query_params.get("published") == "true":
            queryset = queryset.filter(is_published=True)
        grade_level = self.request.query_params.get("grade_level")
        skill_id = self.request.query_params.get("skill")
        if grade_level:
            queryset = queryset.filter(grade_level=grade_level)
        if skill_id:
            queryset = queryset.filter(skill_id=skill_id)
        return queryset

    @action(detail=False, methods=["get"], url_path="personalized")
    def personalized(self, request):
        child_id = request.query_params.get("child")
        if not child_id:
            return Response({"child": "This query parameter is required."}, status=400)

        child = ChildProfile.objects.select_related("school").get(id=child_id, is_deleted=False)
        if not has_coppa_consent(child):
            return Response({"detail": "COPPA consent is required before personalizing lessons."}, status=403)

        mastered_skill_ids = Progress.objects.filter(
            child=child,
            status=Progress.Status.MASTERED,
            is_deleted=False,
        ).values_list("skill_id", flat=True)
        developing_skill_ids = Progress.objects.filter(
            child=child,
            status__in=[Progress.Status.NOT_STARTED, Progress.Status.EMERGING, Progress.Status.DEVELOPING],
            is_deleted=False,
        ).values_list("skill_id", flat=True)

        queryset = self.get_queryset().filter(is_published=True)
        if developing_skill_ids:
            queryset = queryset.filter(skill_id__in=developing_skill_ids)
        else:
            queryset = queryset.exclude(skill_id__in=mastered_skill_ids)
        if child.grade_level:
            queryset = queryset.filter(grade_level__in=["", child.grade_level])

        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(LessonSerializer(page, many=True, context=self.get_serializer_context()).data)
        return Response(LessonSerializer(queryset, many=True, context=self.get_serializer_context()).data)


class TeachingAidViewSet(viewsets.ModelViewSet):
    serializer_class = TeachingAidSerializer
    permission_classes = [IsAuthenticated]
    queryset = TeachingAid.objects.filter(is_deleted=False).select_related("lesson", "skill")

```

## `apps/progress/views.py`
```python
from django.db.models import Avg, Count
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import COPPAConsentRequired, has_coppa_consent
from apps.progress.models import MasteryRecord, Progress
from apps.progress.serializers import MasteryRecordSerializer, ProgressSerializer
from apps.users.models import ChildProfile


class ProgressViewSet(viewsets.ModelViewSet):
    serializer_class = ProgressSerializer
    permission_classes = [IsAuthenticated, COPPAConsentRequired]

    def get_queryset(self):
        queryset = Progress.objects.filter(is_deleted=False).select_related("child", "skill", "school", "last_assessment")
        child_id = self.request.query_params.get("child")
        skill_id = self.request.query_params.get("skill")
        status_value = self.request.query_params.get("status")
        if child_id:
            queryset = queryset.filter(child_id=child_id)
        if skill_id:
            queryset = queryset.filter(skill_id=skill_id)
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        child_id = request.query_params.get("child")
        if not child_id:
            return Response({"child": "This query parameter is required."}, status=400)

        child = ChildProfile.objects.get(id=child_id, is_deleted=False)
        if not has_coppa_consent(child):
            return Response({"detail": "COPPA consent is required before viewing the progress dashboard."}, status=403)

        records = self.get_queryset().filter(child=child)
        by_status = records.values("status").annotate(count=Count("id")).order_by("status")
        by_domain = records.values("skill__domain").annotate(count=Count("id"), average_score=Avg("current_score")).order_by("skill__domain")
        recent_mastery = MasteryRecord.objects.filter(child=child, is_deleted=False).select_related("skill").order_by("-mastered_at")[:10]

        return Response(
            {
                "child": child_id,
                "summary": {
                    "total_skills": records.count(),
                    "mastered": records.filter(status=Progress.Status.MASTERED).count(),
                    "developing": records.filter(status__in=[Progress.Status.EMERGING, Progress.Status.DEVELOPING]).count(),
                },
                "by_status": list(by_status),
                "by_domain": list(by_domain),
                "recent_mastery": MasteryRecordSerializer(recent_mastery, many=True, context=self.get_serializer_context()).data,
            }
        )


class MasteryRecordViewSet(viewsets.ModelViewSet):
    serializer_class = MasteryRecordSerializer
    permission_classes = [IsAuthenticated, COPPAConsentRequired]

    def get_queryset(self):
        queryset = MasteryRecord.objects.filter(is_deleted=False).select_related("child", "skill", "progress", "assessment", "mastered_by")
        child_id = self.request.query_params.get("child")
        if child_id:
            queryset = queryset.filter(child_id=child_id)
        return queryset

```

## `apps/crm/views.py`
```python
from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import IsSchoolAdmin
from apps.crm.models import Lead, Opportunity
from apps.crm.serializers import LeadSerializer, OpportunitySerializer
from apps.schools.models import School
from apps.users.models import AuditLog


class LeadViewSet(viewsets.ModelViewSet):
    serializer_class = LeadSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Lead.objects.filter(is_deleted=False).select_related("assigned_to")
        status_value = self.request.query_params.get("status")
        assigned_to = self.request.query_params.get("assigned_to")
        if status_value:
            queryset = queryset.filter(status=status_value)
        if assigned_to:
            queryset = queryset.filter(assigned_to_id=assigned_to)
        return queryset

    def perform_create(self, serializer):
        serializer.save(assigned_to=serializer.validated_data.get("assigned_to") or self.request.user)

    @action(detail=True, methods=["post"])
    def qualify(self, request, pk=None):
        lead = self.get_object()
        lead.status = Lead.Status.QUALIFIED
        lead.save(update_fields=["status", "updated_at"])
        return Response(LeadSerializer(lead, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def convert(self, request, pk=None):
        lead = self.get_object()
        with transaction.atomic():
            opportunity = Opportunity.objects.create(
                lead=lead,
                owner=request.user,
                name=request.data.get("name", f"{lead.school_name} Opportunity"),
                stage=Opportunity.Stage.DISCOVERY,
                value=request.data.get("value", 0),
                probability=request.data.get("probability", 10),
                expected_close_date=request.data.get("expected_close_date") or None,
                metadata={"converted_from_lead_id": lead.id},
            )
            lead.status = Lead.Status.CONVERTED
            lead.save(update_fields=["status", "updated_at"])
            AuditLog.objects.create(
                actor=request.user,
                action="lead.converted",
                entity_type="Opportunity",
                entity_id=str(opportunity.id),
                after={"lead_id": lead.id},
            )
        return Response(OpportunitySerializer(opportunity, context=self.get_serializer_context()).data, status=status.HTTP_201_CREATED)


class OpportunityViewSet(viewsets.ModelViewSet):
    serializer_class = OpportunitySerializer
    permission_classes = [IsAuthenticated, IsSchoolAdmin]

    def get_queryset(self):
        queryset = Opportunity.objects.filter(is_deleted=False).select_related("lead", "school", "owner")
        stage = self.request.query_params.get("stage")
        owner = self.request.query_params.get("owner")
        if stage:
            queryset = queryset.filter(stage=stage)
        if owner:
            queryset = queryset.filter(owner_id=owner)
        return queryset

    @action(detail=True, methods=["post"])
    def advance(self, request, pk=None):
        opportunity = self.get_object()
        stage = request.data.get("stage")
        if stage not in Opportunity.Stage.values:
            return Response({"stage": "Invalid opportunity stage."}, status=status.HTTP_400_BAD_REQUEST)
        opportunity.stage = stage
        opportunity.probability = request.data.get("probability", opportunity.probability)
        opportunity.next_steps = request.data.get("next_steps", opportunity.next_steps)
        if stage in {Opportunity.Stage.WON, Opportunity.Stage.LOST}:
            opportunity.closed_at = timezone.now()
        if stage == Opportunity.Stage.LOST:
            opportunity.lost_reason = request.data.get("lost_reason", opportunity.lost_reason)
        school_id = request.data.get("school")
        if school_id:
            opportunity.school = School.objects.get(id=school_id)
        opportunity.save()
        return Response(OpportunitySerializer(opportunity, context=self.get_serializer_context()).data)

```

## `apps/assessments/tasks.py`
```python
from celery import shared_task


@shared_task
def notify_assessment_review_completed(assessment_id):
    from apps.assessments.models import Assessment
    from apps.users.models import AuditLog

    assessment = (
        Assessment.objects.select_related("child", "assigned_by", "skill")
        .filter(id=assessment_id)
        .first()
    )
    if assessment is None:
        return {"status": "missing", "assessment_id": assessment_id}

    AuditLog.objects.create(
        actor=assessment.assigned_by,
        action="assessment.review_completed.notification",
        entity_type="Assessment",
        entity_id=str(assessment.id),
        after={
            "child_id": assessment.child_id,
            "skill_id": assessment.skill_id,
            "status": assessment.status,
        },
        metadata={"notification": "assessment_review_completed"},
    )
    return {"status": "queued", "assessment_id": assessment.id}

```

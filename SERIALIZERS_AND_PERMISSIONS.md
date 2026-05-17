# Clear Code Reading Serializers and Permissions

## `apps/api/permissions.py`
```python
from django.utils import timezone
from rest_framework.permissions import BasePermission, SAFE_METHODS

from apps.schools.models import SchoolMembership
from apps.users.models import CustomUser, GuardianRelationship


EVALUATOR_ROLES = {
    CustomUser.Role.SUPER_ADMIN,
    CustomUser.Role.SCHOOL_ADMIN,
    CustomUser.Role.TEACHER,
}

SCHOOL_ADMIN_ROLES = {
    CustomUser.Role.SUPER_ADMIN,
    CustomUser.Role.SCHOOL_ADMIN,
}

CONSENT_REQUIRED_TYPES = {"assessment", "data_processing", "school_sharing"}


def get_child_from_obj(obj):
    if hasattr(obj, "child"):
        return obj.child
    if hasattr(obj, "child_profile"):
        return obj.child_profile
    return obj


def has_school_membership(user, school, roles=None):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, "role", None) == CustomUser.Role.SUPER_ADMIN:
        return True
    if school is None:
        return False

    queryset = SchoolMembership.objects.filter(user=user, school=school, is_deleted=False)
    if roles is not None:
        queryset = queryset.filter(role__in=roles)
    return queryset.exists()


def is_parent_of_child(user, child):
    if not user or not user.is_authenticated or child is None:
        return False
    return GuardianRelationship.objects.filter(
        guardian=user,
        child=child,
        is_deleted=False,
        consent_status=GuardianRelationship.ConsentStatus.GRANTED,
    ).filter(
        models_consent_filter()
    ).exists()


def models_consent_filter():
    from django.db.models import Q

    return Q(consent_expires_at__isnull=True) | Q(consent_expires_at__gt=timezone.now())


def has_coppa_consent(child, consent_types=None):
    if child is None:
        return False

    now = timezone.now()
    relationships = GuardianRelationship.objects.filter(
        child=child,
        is_deleted=False,
        consent_status=GuardianRelationship.ConsentStatus.GRANTED,
    ).filter(
        models_consent_filter()
    )

    if not relationships.exists():
        return False

    consent_types = set(consent_types or CONSENT_REQUIRED_TYPES)
    if not consent_types:
        return True

    from apps.users.models import ConsentLog

    for consent_type in consent_types:
        if not ConsentLog.objects.filter(
            child=child,
            consent_type=consent_type,
            status=ConsentLog.Status.GRANTED,
        ).filter(
            models_consent_filter_for_log(now)
        ).exists():
            return False

    return True


def models_consent_filter_for_log(now):
    from django.db.models import Q

    return Q(expires_at__isnull=True) | Q(expires_at__gt=now)


def user_can_evaluate_child(user, child):
    if not user or not user.is_authenticated or child is None:
        return False
    if user.is_superuser or getattr(user, "role", None) in EVALUATOR_ROLES:
        return child.school is None or has_school_membership(user, child.school)
    return False


class IsParentOfChild(BasePermission):
    message = "You must be an approved guardian for this child."

    def has_object_permission(self, request, view, obj):
        child = get_child_from_obj(obj)
        return is_parent_of_child(request.user, child)


class IsEvaluator(BasePermission):
    message = "You must be an evaluator assigned to this child's school."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and getattr(user, "role", None) in EVALUATOR_ROLES)

    def has_object_permission(self, request, view, obj):
        child = get_child_from_obj(obj)
        return user_can_evaluate_child(request.user, child)


class IsSchoolAdmin(BasePermission):
    message = "You must be a school administrator."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and getattr(user, "role", None) in SCHOOL_ADMIN_ROLES)

    def has_object_permission(self, request, view, obj):
        school = getattr(obj, "school", obj)
        return has_school_membership(
            request.user,
            school,
            roles=[SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN],
        )


class COPPAConsentRequired(BasePermission):
    message = "COPPA consent is required before accessing or modifying this child's records."

    def has_object_permission(self, request, view, obj):
        child = get_child_from_obj(obj)
        if request.method in SAFE_METHODS and is_parent_of_child(request.user, child):
            return True
        return has_coppa_consent(child)

```

## `apps/api/serializers.py`
```python
from rest_framework import serializers

from apps.api.permissions import CONSENT_REQUIRED_TYPES, has_coppa_consent
from apps.curriculum.models import Skill
from apps.schools.models import School
from apps.users.models import ChildProfile, CustomUser


class COPPAConsentMixin:
    coppa_child_field = "child"
    coppa_consent_types = CONSENT_REQUIRED_TYPES

    def _get_child_for_coppa(self, attrs):
        child = attrs.get(self.coppa_child_field)
        if child is not None:
            return child
        instance = getattr(self, "instance", None)
        if instance is not None:
            return getattr(instance, self.coppa_child_field, None)
        return None

    def validate(self, attrs):
        attrs = super().validate(attrs)
        child = self._get_child_for_coppa(attrs)
        if child is not None and not has_coppa_consent(child, self.coppa_consent_types):
            raise serializers.ValidationError(
                {
                    self.coppa_child_field: (
                        "COPPA consent is required before creating or updating "
                        "assessment, progress, or child learning records."
                    )
                }
            )
        return attrs


class UserSummarySerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = CustomUser
        fields = ["id", "email", "first_name", "last_name", "full_name", "role"]
        read_only_fields = fields


class SchoolSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = School
        fields = ["id", "name", "slug", "district"]
        read_only_fields = fields


class ChildSummarySerializer(serializers.ModelSerializer):
    school = SchoolSummarySerializer(read_only=True)

    class Meta:
        model = ChildProfile
        fields = ["id", "first_name", "last_name", "grade_level", "school"]
        read_only_fields = fields


class SkillSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Skill
        fields = ["id", "code", "name", "domain", "grade_band"]
        read_only_fields = fields

```

## `apps/users/serializers.py`
```python
from rest_framework import serializers

from apps.api.permissions import has_coppa_consent
from apps.api.serializers import SchoolSummarySerializer, UserSummarySerializer
from apps.schools.models import School
from apps.users.models import AuditLog, ChildProfile, ConsentLog, CustomUser, GuardianRelationship, Profile


class ProfileSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(),
        source="user",
        write_only=True,
        required=False,
    )

    class Meta:
        model = Profile
        fields = [
            "id",
            "user",
            "user_id",
            "display_name",
            "avatar",
            "timezone",
            "preferences",
            "onboarding_completed_at",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "is_deleted", "deleted_at", "created_at", "updated_at"]


class CustomUserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(read_only=True)
    full_name = serializers.CharField(source="get_full_name", read_only=True)
    password = serializers.CharField(write_only=True, required=False, trim_whitespace=False)

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "username",
            "email",
            "password",
            "first_name",
            "last_name",
            "full_name",
            "role",
            "phone_number",
            "metadata",
            "profile",
            "is_active",
            "is_staff",
            "is_superuser",
            "last_login",
            "date_joined",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "full_name",
            "profile",
            "is_staff",
            "is_superuser",
            "last_login",
            "date_joined",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {"metadata": {"write_only": True, "required": False}}

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = CustomUser(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class ChildProfileSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.filter(role=CustomUser.Role.STUDENT),
        source="user",
        write_only=True,
        required=False,
        allow_null=True,
    )
    school = SchoolSummarySerializer(read_only=True)
    school_id = serializers.PrimaryKeyRelatedField(
        queryset=School.objects.all(),
        source="school",
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = ChildProfile
        fields = [
            "id",
            "user",
            "user_id",
            "first_name",
            "last_name",
            "date_of_birth",
            "grade_level",
            "school",
            "school_id",
            "student_identifier",
            "learning_profile",
            "accommodations",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "school", "is_deleted", "deleted_at", "created_at", "updated_at"]
        extra_kwargs = {
            "date_of_birth": {"write_only": True},
            "student_identifier": {"write_only": True},
        }

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        child = instance
        sensitive_updates = {"learning_profile", "accommodations"} & set(attrs.keys())
        if child is not None and sensitive_updates and not has_coppa_consent(child):
            raise serializers.ValidationError("COPPA consent is required before updating child learning data.")
        return attrs


class GuardianRelationshipSerializer(serializers.ModelSerializer):
    guardian = UserSummarySerializer(read_only=True)
    guardian_id = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.filter(role=CustomUser.Role.GUARDIAN),
        source="guardian",
        write_only=True,
    )
    child = ChildProfileSerializer(read_only=True)
    child_id = serializers.PrimaryKeyRelatedField(queryset=ChildProfile.objects.all(), source="child", write_only=True)

    class Meta:
        model = GuardianRelationship
        fields = [
            "id",
            "guardian",
            "guardian_id",
            "child",
            "child_id",
            "relationship_type",
            "is_primary",
            "consent_status",
            "consent_expires_at",
            "permissions",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "guardian",
            "child",
            "consent_status",
            "consent_expires_at",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]


class ConsentLogSerializer(serializers.ModelSerializer):
    guardian_relationship = GuardianRelationshipSerializer(read_only=True)
    guardian_relationship_id = serializers.PrimaryKeyRelatedField(
        queryset=GuardianRelationship.objects.all(),
        source="guardian_relationship",
        write_only=True,
        required=False,
        allow_null=True,
    )
    guardian = UserSummarySerializer(read_only=True)
    child = ChildProfileSerializer(read_only=True)

    class Meta:
        model = ConsentLog
        fields = [
            "id",
            "guardian_relationship",
            "guardian_relationship_id",
            "guardian",
            "child",
            "consent_type",
            "status",
            "version",
            "source",
            "ip_address",
            "user_agent",
            "expires_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "guardian_relationship", "guardian", "child", "created_at", "updated_at"]
        extra_kwargs = {
            "ip_address": {"write_only": True, "required": False},
            "user_agent": {"write_only": True, "required": False},
        }

    def validate(self, attrs):
        relationship = attrs.get("guardian_relationship")
        if relationship:
            attrs.setdefault("guardian", relationship.guardian)
            attrs.setdefault("child", relationship.child)
        if not attrs.get("guardian") or not attrs.get("child"):
            raise serializers.ValidationError("Consent logs must be tied to a guardian relationship.")
        return attrs


class AuditLogSerializer(serializers.ModelSerializer):
    actor = UserSummarySerializer(read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "actor",
            "action",
            "entity_type",
            "entity_id",
            "before",
            "after",
            "metadata",
            "ip_address",
            "user_agent",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

```

## `apps/schools/serializers.py`
```python
from rest_framework import serializers

from apps.api.serializers import UserSummarySerializer
from apps.schools.models import School, SchoolMembership
from apps.users.models import CustomUser


class SchoolMembershipSerializer(serializers.ModelSerializer):
    school_name = serializers.CharField(source="school.name", read_only=True)
    user = UserSummarySerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(queryset=CustomUser.objects.all(), source="user", write_only=True)
    invited_by = UserSummarySerializer(read_only=True)
    invited_by_id = serializers.PrimaryKeyRelatedField(
        queryset=CustomUser.objects.all(),
        source="invited_by",
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = SchoolMembership
        fields = [
            "id",
            "school",
            "school_name",
            "user",
            "user_id",
            "role",
            "title",
            "permissions",
            "invited_by",
            "invited_by_id",
            "joined_at",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "school_name",
            "user",
            "invited_by",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]


class SchoolSerializer(serializers.ModelSerializer):
    memberships = SchoolMembershipSerializer(many=True, read_only=True)

    class Meta:
        model = School
        fields = [
            "id",
            "schema_name",
            "name",
            "slug",
            "district",
            "address",
            "contact_email",
            "contact_phone",
            "settings",
            "branding",
            "paid_until",
            "on_trial",
            "memberships",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "memberships", "is_deleted", "deleted_at", "created_at", "updated_at"]
        extra_kwargs = {
            "settings": {"write_only": True, "required": False},
        }

```

## `apps/assessments/serializers.py`
```python
from rest_framework import serializers

from apps.api.serializers import COPPAConsentMixin, ChildSummarySerializer, SkillSummarySerializer, UserSummarySerializer
from apps.assessments.models import Assessment
from apps.schools.serializers import SchoolSerializer


class AssessmentSerializer(COPPAConsentMixin, serializers.ModelSerializer):
    child_detail = ChildSummarySerializer(source="child", read_only=True)
    school_detail = SchoolSerializer(source="school", read_only=True)
    assigned_by_detail = UserSummarySerializer(source="assigned_by", read_only=True)
    skill_detail = SkillSummarySerializer(source="skill", read_only=True)
    percent_score = serializers.SerializerMethodField()

    class Meta:
        model = Assessment
        fields = [
            "id",
            "child",
            "child_detail",
            "school",
            "school_detail",
            "assigned_by",
            "assigned_by_detail",
            "assessment_type",
            "status",
            "title",
            "skill",
            "skill_detail",
            "scheduled_for",
            "started_at",
            "completed_at",
            "raw_score",
            "max_score",
            "percent_score",
            "percentile",
            "responses",
            "scoring",
            "recommendations",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "child_detail",
            "school_detail",
            "assigned_by_detail",
            "skill_detail",
            "percent_score",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]

    def get_percent_score(self, obj):
        if obj.raw_score is None or not obj.max_score:
            return None
        return round((obj.raw_score / obj.max_score) * 100, 2)

```

## `apps/curriculum/serializers.py`
```python
from rest_framework import serializers

from apps.curriculum.models import Lesson, Skill, TeachingAid


class SkillSerializer(serializers.ModelSerializer):
    prerequisites = serializers.PrimaryKeyRelatedField(queryset=Skill.objects.all(), many=True, required=False)
    prerequisite_details = serializers.SerializerMethodField()

    class Meta:
        model = Skill
        fields = [
            "id",
            "code",
            "name",
            "domain",
            "grade_band",
            "description",
            "prerequisites",
            "prerequisite_details",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "prerequisite_details", "is_deleted", "deleted_at", "created_at", "updated_at"]

    def get_prerequisite_details(self, obj):
        return [{"id": skill.id, "code": skill.code, "name": skill.name} for skill in obj.prerequisites.all()]


class TeachingAidSerializer(serializers.ModelSerializer):
    lesson_title = serializers.CharField(source="lesson.title", read_only=True)
    skill_code = serializers.CharField(source="skill.code", read_only=True)

    class Meta:
        model = TeachingAid
        fields = [
            "id",
            "lesson",
            "lesson_title",
            "skill",
            "skill_code",
            "title",
            "aid_type",
            "file",
            "url",
            "content",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "lesson_title", "skill_code", "is_deleted", "deleted_at", "created_at", "updated_at"]

    def validate(self, attrs):
        if not attrs.get("lesson") and not attrs.get("skill"):
            raise serializers.ValidationError("A teaching aid must be attached to a lesson or skill.")
        return attrs


class LessonSerializer(serializers.ModelSerializer):
    skill_detail = SkillSerializer(source="skill", read_only=True)
    teaching_aids = TeachingAidSerializer(many=True, read_only=True)

    class Meta:
        model = Lesson
        fields = [
            "id",
            "title",
            "slug",
            "skill",
            "skill_detail",
            "grade_level",
            "duration_minutes",
            "objective",
            "content",
            "materials",
            "differentiation",
            "is_published",
            "teaching_aids",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "skill_detail", "teaching_aids", "is_deleted", "deleted_at", "created_at", "updated_at"]

```

## `apps/progress/serializers.py`
```python
from rest_framework import serializers

from apps.api.serializers import COPPAConsentMixin, ChildSummarySerializer, SkillSummarySerializer, UserSummarySerializer
from apps.assessments.serializers import AssessmentSerializer
from apps.progress.models import MasteryRecord, Progress
from apps.schools.serializers import SchoolSerializer


class ProgressSerializer(COPPAConsentMixin, serializers.ModelSerializer):
    child_detail = ChildSummarySerializer(source="child", read_only=True)
    skill_detail = SkillSummarySerializer(source="skill", read_only=True)
    school_detail = SchoolSerializer(source="school", read_only=True)
    last_assessment_detail = AssessmentSerializer(source="last_assessment", read_only=True)

    class Meta:
        model = Progress
        fields = [
            "id",
            "child",
            "child_detail",
            "skill",
            "skill_detail",
            "school",
            "school_detail",
            "status",
            "current_score",
            "target_score",
            "attempts",
            "last_assessment",
            "last_assessment_detail",
            "evidence",
            "notes",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "child_detail",
            "skill_detail",
            "school_detail",
            "last_assessment_detail",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]


class MasteryRecordSerializer(COPPAConsentMixin, serializers.ModelSerializer):
    child_detail = ChildSummarySerializer(source="child", read_only=True)
    skill_detail = SkillSummarySerializer(source="skill", read_only=True)
    progress_detail = ProgressSerializer(source="progress", read_only=True)
    assessment_detail = AssessmentSerializer(source="assessment", read_only=True)
    mastered_by_detail = UserSummarySerializer(source="mastered_by", read_only=True)

    class Meta:
        model = MasteryRecord
        fields = [
            "id",
            "child",
            "child_detail",
            "skill",
            "skill_detail",
            "progress",
            "progress_detail",
            "assessment",
            "assessment_detail",
            "mastered_at",
            "mastered_by",
            "mastered_by_detail",
            "score",
            "evidence",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "child_detail",
            "skill_detail",
            "progress_detail",
            "assessment_detail",
            "mastered_by_detail",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        progress = attrs.get("progress") or getattr(self.instance, "progress", None)
        child = attrs.get("child") or getattr(self.instance, "child", None)
        skill = attrs.get("skill") or getattr(self.instance, "skill", None)
        if progress and child and progress.child_id != child.id:
            raise serializers.ValidationError({"progress": "Progress record must belong to the same child."})
        if progress and skill and progress.skill_id != skill.id:
            raise serializers.ValidationError({"progress": "Progress record must track the same skill."})
        return attrs

```

## `apps/crm/serializers.py`
```python
from rest_framework import serializers

from apps.api.serializers import SchoolSummarySerializer, UserSummarySerializer
from apps.crm.models import Lead, Opportunity


class LeadSerializer(serializers.ModelSerializer):
    assigned_to_detail = UserSummarySerializer(source="assigned_to", read_only=True)

    class Meta:
        model = Lead
        fields = [
            "id",
            "school_name",
            "contact_name",
            "contact_email",
            "contact_phone",
            "source",
            "status",
            "assigned_to",
            "assigned_to_detail",
            "estimated_students",
            "notes",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "assigned_to_detail", "is_deleted", "deleted_at", "created_at", "updated_at"]
        extra_kwargs = {
            "contact_email": {"write_only": True},
            "contact_phone": {"write_only": True, "required": False},
        }


class OpportunitySerializer(serializers.ModelSerializer):
    lead_detail = LeadSerializer(source="lead", read_only=True)
    school_detail = SchoolSummarySerializer(source="school", read_only=True)
    owner_detail = UserSummarySerializer(source="owner", read_only=True)

    class Meta:
        model = Opportunity
        fields = [
            "id",
            "lead",
            "lead_detail",
            "school",
            "school_detail",
            "owner",
            "owner_detail",
            "name",
            "stage",
            "value",
            "probability",
            "expected_close_date",
            "closed_at",
            "lost_reason",
            "next_steps",
            "metadata",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "lead_detail",
            "school_detail",
            "owner_detail",
            "is_deleted",
            "deleted_at",
            "created_at",
            "updated_at",
        ]

    def validate_probability(self, value):
        if value > 100:
            raise serializers.ValidationError("Probability must be between 0 and 100.")
        return value

```

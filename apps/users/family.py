"""Signed reader onboarding in the family portal."""

from uuid import uuid4

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.crm.parent_contacts import ensure_parent_contact
from apps.users.family_teachers import inherited_teacher_fields
from apps.users.models import (
    AuditLog,
    ChildProfile,
    ConsentLog,
    CustomUser,
    GuardianRelationship,
    SignedReaderForms,
)
from apps.users.onboarding_views import PortalRequest

FORM_VERSION = "reader-forms-v1"
STATEMENTS = {
    "data_processing": "I am this reader’s parent or legal guardian. I authorize ClearCode Reading to collect and use their reader information to provide reading instruction and support.",
    "assessment": "Allow ClearCode Reading to assess my child’s reading.",
    "school_sharing": "Allow ClearCode Reading to share my child’s reading progress with their school.",
    "filming": "I authorize ClearCode Reading to photograph, film and audio-record my child during program activities and to use their image, voice and recordings for internal instruction, staff training and review, and for public marketing, including the ClearCode Reading website, social media and promotional materials.",
    "signature": "By typing my name and signing, I agree to sign these forms electronically and confirm the choices shown above.",
}


def editable_family_relationships(parent: CustomUser):
    """Relationships eligible for the guardian profile or its signed-forms step."""
    if not parent.can_add_child or not parent.pk:
        return GuardianRelationship.objects.none()
    return (
        GuardianRelationship.objects.filter(
            guardian=parent,
            is_deleted=False,
            child__is_deleted=False,
            consent_status__in=[
                GuardianRelationship.ConsentStatus.GRANTED,
                GuardianRelationship.ConsentStatus.PENDING,
            ],
        )
        .filter(
            Q(consent_expires_at__isnull=True)
            | Q(consent_expires_at__gt=timezone.now())
        )
        .select_related("child")
    )


def family_relationship(
    parent: CustomUser, child_id: int, *, lock: bool = False
) -> GuardianRelationship:
    if not parent.can_add_child:
        raise PermissionDenied
    relationships = editable_family_relationships(parent).filter(child_id=child_id)
    if lock:
        relationships = relationships.select_for_update()
    return get_object_or_404(relationships)


class ReaderForms(forms.Form):
    submission_id = forms.UUIDField(initial=uuid4, widget=forms.HiddenInput)
    first_name = forms.CharField(label="Reader’s first name", max_length=120)
    relationship_type = forms.ChoiceField(
        label="Your relationship",
        choices=[
            (GuardianRelationship.RelationshipType.PARENT, "Parent"),
            (GuardianRelationship.RelationshipType.LEGAL_GUARDIAN, "Legal guardian"),
        ],
    )
    data_processing = forms.BooleanField(label=STATEMENTS["data_processing"])
    assessment = forms.BooleanField(label=STATEMENTS["assessment"], required=False)
    school_sharing = forms.BooleanField(
        label=STATEMENTS["school_sharing"], required=False
    )
    filming = forms.ChoiceField(
        label="Filming permission",
        choices=[
            ("", "Choose your answer"),
            ("allow", "I give permission"),
            ("decline", "I decline"),
        ],
    )
    signed_name = forms.CharField(label="Your full name", max_length=200)
    electronic_signature = forms.BooleanField(label=STATEMENTS["signature"])

    @transaction.atomic
    def sign(self, parent: CustomUser, child_id: int | None) -> int:
        parent = CustomUser.objects.select_for_update().get(pk=parent.pk)
        if not parent.can_add_child:
            raise PermissionDenied
        data = self.cleaned_data
        if child_id is None:
            previous = SignedReaderForms.objects.filter(
                relationship__guardian=parent, submission_id=data["submission_id"]
            ).first()
            if previous:
                return previous.relationship.child_id
            ensure_parent_contact(parent)
            child = ChildProfile.objects.create(
                first_name=data["first_name"], learning_profile=inherited_teacher_fields(parent)
            )
            relationship = GuardianRelationship.objects.create(
                guardian=parent,
                child=child,
                is_primary=True,
                relationship_type=data["relationship_type"],
                # Consent was signed in this transaction. A pending-consent reminder would be incorrect.
                consent_status=GuardianRelationship.ConsentStatus.GRANTED,
            )
        else:
            relationship = family_relationship(parent, child_id, lock=True)
            if SignedReaderForms.objects.filter(relationship=relationship).exists():
                return child_id
            child = relationship.child
        receipt = SignedReaderForms.objects.create(
            relationship=relationship,
            submission_id=data["submission_id"],
            signed_name=data["signed_name"],
            signer_email=parent.email,
            reader_name=str(child),
            version=FORM_VERSION,
            statements=STATEMENTS,
            assessment_allowed=data["assessment"],
            school_sharing_allowed=data["school_sharing"],
            filming_allowed=data["filming"] == "allow",
        )
        for consent_type in (
            ConsentLog.ConsentType.ASSESSMENT,
            ConsentLog.ConsentType.SCHOOL_SHARING,
            ConsentLog.ConsentType.DATA_PROCESSING,
        ):
            previously_granted = (
                not data[consent_type]
                and ConsentLog.objects.filter(
                    guardian=parent,
                    child=child,
                    consent_type=consent_type,
                    status=ConsentLog.Status.GRANTED,
                ).exists()
            )
            if data[consent_type] or previously_granted:
                ConsentLog.objects.create(
                    guardian=parent,
                    child=child,
                    guardian_relationship=relationship,
                    consent_type=consent_type,
                    status=ConsentLog.Status.GRANTED
                    if data[consent_type]
                    else ConsentLog.Status.REVOKED,
                    source="parent_portal",
                    version=FORM_VERSION,
                )
        AuditLog.objects.create(
            actor=parent,
            action="reader.forms_signed",
            entity_type="SignedReaderForms",
            entity_id=str(receipt.pk),
            after={"child_id": child.pk},
        )
        return child.pk


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def add_child(request: PortalRequest, child_id: int | None = None) -> HttpResponse:
    if not request.user.can_add_child:
        raise PermissionDenied
    relationship = (
        family_relationship(request.user, child_id) if child_id is not None else None
    )
    if relationship and hasattr(relationship, "signed_forms"):
        return render(
            request,
            "portal/signed_reader_forms.html",
            {"receipt": relationship.signed_forms},
        )
    starting = request.method == "POST" and request.POST.get("step") == "start"
    initial = (
        {"first_name": request.POST.get("first_name", "")[:120]} if starting else {}
    )
    form = ReaderForms(
        request.POST if request.method == "POST" and not starting else None,
        initial=initial,
    )
    if relationship:
        form.fields["first_name"].initial = relationship.child.first_name
        form.fields["first_name"].disabled = True
        # A guardian signs for the existing relationship without changing its recorded type.
        form.fields.pop("relationship_type")
    if form.is_bound and form.is_valid():
        reader_id = form.sign(request.user, child_id)
        messages.success(
            request, "Your signed forms are saved. Continue with your Reader Profile."
        )
        return redirect("portal_reader_profile", child_id=reader_id)
    return render(
        request,
        "portal/add_child.html",
        {"form": form, "filming_statement": STATEMENTS["filming"]},
    )

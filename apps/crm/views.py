from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import IsSchoolAdmin
from apps.crm.models import Lead, NewsletterSubscription, Opportunity
from apps.crm.newsletters import resolve_unsubscribe_token
from apps.crm.serializers import LeadSerializer, OpportunitySerializer
from apps.schools.models import School
from apps.users.models import AuditLog, CustomUser


class WebsiteSignupView(View):
    def post(self, request):
        contact_name = request.POST.get("name", "").strip()
        contact_email = request.POST.get("email", "").strip().lower()
        career_path = request.POST.get("career_path", "").strip()
        role_interest = request.POST.get("role_interest", "").strip()[:255]
        is_career_signup = request.POST.get("redirect_to") == "/careers/"
        career_fields_are_missing = is_career_signup and (
            career_path not in {"teacher", "company"} or not role_interest
        )
        if not contact_name or not contact_email or career_fields_are_missing:
            messages.error(request, "Please complete the required fields so we can follow up.")
            return redirect(self._redirect_target(request, "missing"))

        audience = request.POST.get("audience", Lead.Audience.PARENT)
        if audience not in Lead.Audience.values:
            audience = Lead.Audience.OTHER

        organization_name = request.POST.get("organization_name", "").strip()
        contact_phone = request.POST.get("phone", "").strip()
        notes = request.POST.get("notes", "").strip()
        if career_path in {"teacher", "company"}:
            audience = Lead.Audience.TEACHER if career_path == "teacher" else Lead.Audience.OTHER
            career_details = [
                f"Career path: {career_path}",
                f"Role interest: {role_interest}" if role_interest else "",
            ]
            notes = "\n".join(part for part in [*career_details, notes] if part)
        child_age_grade = request.POST.get("child_age_grade", "").strip()
        if child_age_grade:
            notes = "\n".join(part for part in [f"Child age or grade: {child_age_grade}", notes] if part)
        estimated_students = self._clean_positive_int(request.POST.get("estimated_students"))
        linked_user = CustomUser.objects.filter(email=contact_email, is_deleted=False).first()
        school_name = self._school_name_for_signup(audience, organization_name)

        lead = Lead.objects.filter(contact_email=contact_email, is_deleted=False).first()
        metadata = {
            **((lead.metadata if lead else {}) or {}),
            "latest_signup_at": timezone.now().isoformat(),
            "latest_signup_audience": audience,
            "source_path": request.path,
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            "career_path": career_path if career_path in {"teacher", "company"} else "",
        }
        defaults = {
            "school_name": school_name,
            "contact_name": contact_name,
            "contact_phone": contact_phone,
            "audience": audience,
            "organization_name": organization_name,
            "source": Lead.Source.WEBSITE,
            "linked_user": linked_user,
            "estimated_students": estimated_students,
            "notes": notes,
            "metadata": metadata,
        }

        if lead is None:
            lead = Lead.objects.create(contact_email=contact_email, **defaults)
            AuditLog.objects.create(
                actor=linked_user,
                action="lead.created_from_website",
                entity_type="Lead",
                entity_id=str(lead.id),
                after={"email": contact_email, "audience": audience},
            )
        else:
            for field, value in defaults.items():
                setattr(lead, field, value)
            if lead.status == Lead.Status.UNQUALIFIED:
                lead.status = Lead.Status.NEW
            lead.save()

        messages.success(request, "Thanks. Your request is with the ClearCode Reading team, and we’ll follow up about next steps.")
        return redirect(self._redirect_target(request, "thanks"))

    @staticmethod
    def _redirect_target(request, result):
        if request.POST.get("redirect_to") == "/careers/":
            return f"/careers/?signup={result}#career-interest-form"
        if request.POST.get("redirect_to") == "/contact/":
            return f"/contact/?signup={result}#consultation-form"
        return f"/?signup={result}#top"

    @staticmethod
    def _clean_positive_int(value):
        try:
            cleaned = int(value)
        except (TypeError, ValueError):
            return None
        return cleaned if cleaned >= 0 else None

    @staticmethod
    def _school_name_for_signup(audience, organization_name):
        if organization_name:
            return organization_name
        if audience == Lead.Audience.PARENT:
            return "Family inquiry"
        if audience == Lead.Audience.TEACHER:
            return "Teacher inquiry"
        if audience == Lead.Audience.SCHOOL:
            return "School or district inquiry"
        return "Website inquiry"


class NewsletterSignupView(View):
    def post(self, request):
        redirect_path = self._redirect_path(request)
        if request.POST.get("website", "").strip():
            return redirect(f"{redirect_path}?newsletter=thanks#newsletter-signup")

        email = request.POST.get("email", "").strip().lower()
        consent_given = request.POST.get("consent") == "yes"
        try:
            if len(email) > 254:
                raise ValidationError("Email address is too long.")
            validate_email(email)
        except ValidationError:
            messages.error(request, "Enter a valid email address to join the newsletter.")
            return redirect(f"{redirect_path}?newsletter=invalid#newsletter-signup")
        if not consent_given:
            messages.error(request, "Please confirm that you would like to receive the newsletter.")
            return redirect(f"{redirect_path}?newsletter=consent#newsletter-signup")

        now = timezone.now()
        submitted_name = request.POST.get("name", "").strip()[:255]
        existing_name = (
            NewsletterSubscription.objects.filter(email=email).values_list("name", flat=True).first()
        )
        NewsletterSubscription.objects.update_or_create(
            email=email,
            defaults={
                "name": submitted_name or existing_name or "",
                "status": NewsletterSubscription.Status.ACTIVE,
                "consented_at": now,
                "unsubscribed_at": None,
                "source_path": redirect_path,
            },
        )

        messages.success(request, "You’re subscribed. Look for ClearCode Reading updates in your inbox.")
        return redirect(f"{redirect_path}?newsletter=thanks#newsletter-signup")

    @staticmethod
    def _redirect_path(request):
        candidate = request.POST.get("redirect_to", "/")
        if len(candidate) > 255 or not candidate.startswith("/") or not url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return "/"
        return candidate.split("?", 1)[0]


@method_decorator(csrf_exempt, name="dispatch")
class NewsletterUnsubscribeView(View):
    template_name = "crm/newsletter_unsubscribe.html"

    def get(self, request, token):
        subscription = resolve_unsubscribe_token(token)
        return render(
            request,
            self.template_name,
            {
                "token": token,
                "subscription": subscription,
                "invalid_link": subscription is None,
                "unsubscribed": bool(
                    subscription and subscription.status == NewsletterSubscription.Status.UNSUBSCRIBED
                ),
            },
        )

    def post(self, request, token):
        subscription = resolve_unsubscribe_token(token)
        invalid_link = subscription is None
        if subscription and subscription.status != NewsletterSubscription.Status.UNSUBSCRIBED:
            subscription.status = NewsletterSubscription.Status.UNSUBSCRIBED
            subscription.unsubscribed_at = timezone.now()
            subscription.save(update_fields=["status", "unsubscribed_at", "updated_at"])
        return render(
            request,
            self.template_name,
            {
                "token": token,
                "subscription": subscription,
                "invalid_link": invalid_link,
                "unsubscribed": not invalid_link,
            },
        )


class LeadViewSet(viewsets.ModelViewSet):
    serializer_class = LeadSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Lead.objects.filter(is_deleted=False).select_related("assigned_to", "linked_user")
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

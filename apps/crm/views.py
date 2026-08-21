from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count, F, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import IsSchoolAdmin
from apps.crm.models import CrmActivity, FormSubmission, Lead, NewsletterSubscription, Opportunity
from apps.crm.newsletters import resolve_unsubscribe_token
from apps.crm.serializers import LeadSerializer, OpportunitySerializer
from apps.crm.services import LeadIntake, record_form_submission, sanitized_submission_data
from apps.schools.models import School
from apps.users.models import AuditLog, CustomUser


class WebsiteSignupView(View):
    def post(self, request):
        contact_name = request.POST.get("name", "").strip()[:255]
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
        try:
            if len(contact_email) > 254:
                raise ValidationError("Email address is too long.")
            validate_email(contact_email)
        except ValidationError:
            messages.error(request, "Enter a valid email address so we can follow up.")
            return redirect(self._redirect_target(request, "invalid"))

        audience = request.POST.get("audience", Lead.Audience.PARENT)
        if audience not in Lead.Audience.values:
            audience = Lead.Audience.OTHER

        organization_name = request.POST.get("organization_name", "").strip()[:255]
        contact_phone = request.POST.get("phone", "").strip()[:32]
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
        school_name = self._school_name_for_signup(audience, organization_name)
        source_path = self._source_path(request, organization_name)
        record_form_submission(
            intake=LeadIntake(
                contact_email=contact_email,
                contact_name=contact_name,
                school_name=school_name,
                audience=audience,
                organization_name=organization_name,
                contact_phone=contact_phone,
                estimated_students=estimated_students,
                notes=notes,
                metadata={
                    "career_path": career_path if career_path in {"teacher", "company"} else "",
                },
            ),
            form_type=self._form_type(request, organization_name),
            source_path=source_path,
            submitted_data=sanitized_submission_data(request.POST),
        )

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

    @staticmethod
    def _form_type(request, organization_name):
        if request.POST.get("redirect_to") == "/careers/":
            return FormSubmission.FormType.CAREER
        if organization_name == "Reading assessment follow-up":
            return FormSubmission.FormType.ASSESSMENT
        if request.POST.get("redirect_to") == "/contact/":
            return FormSubmission.FormType.CONSULTATION
        return FormSubmission.FormType.WEBSITE

    @staticmethod
    def _source_path(request, organization_name):
        if organization_name == "Reading assessment follow-up":
            return "/assessment/"
        candidate = request.POST.get("redirect_to", "/")
        return candidate if candidate in {"/", "/careers/", "/contact/"} else "/"


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
        with transaction.atomic():
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
            record_form_submission(
                intake=LeadIntake(
                    contact_email=email,
                    contact_name=submitted_name or existing_name or email.split("@", 1)[0],
                    school_name="Newsletter subscriber",
                    audience=Lead.Audience.OTHER,
                ),
                form_type=FormSubmission.FormType.NEWSLETTER,
                source_path=redirect_path,
                submitted_data=sanitized_submission_data(request.POST),
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


class IsCrmAdmin(BasePermission):
    message = "You must be a central CRM administrator."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (
                user.is_superuser
                or user.is_staff
                or user.role == CustomUser.Role.SUPER_ADMIN
            )
        )


class LeadViewSet(viewsets.ModelViewSet):
    serializer_class = LeadSerializer
    permission_classes = [IsAuthenticated, IsCrmAdmin]

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


def crm_owner_queryset():
    return CustomUser.objects.filter(is_active=True, is_deleted=False).filter(
        Q(is_staff=True) | Q(role__in=[CustomUser.Role.SUPER_ADMIN, CustomUser.Role.SCHOOL_ADMIN])
    ).distinct().order_by("first_name", "last_name", "email")


class CrmAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    raise_exception = True

    def test_func(self):
        user = self.request.user
        return bool(
            user.is_superuser
            or user.is_staff
            or user.role == CustomUser.Role.SUPER_ADMIN
        )

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return redirect_to_login(
                self.request.get_full_path(),
                self.get_login_url(),
                self.get_redirect_field_name(),
            )
        raise PermissionDenied(self.get_permission_denied_message())


class CrmContactListView(CrmAccessMixin, TemplateView):
    template_name = "crm/contact_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_contacts = Lead.objects.filter(is_deleted=False)
        contacts = all_contacts.select_related("assigned_to").annotate(
            submission_count=Count("form_submissions", distinct=True),
            last_submission_at=Max("form_submissions__created_at"),
        )

        query = self.request.GET.get("q", "").strip()[:255]
        status_filter = self.request.GET.get("status", "")
        audience_filter = self.request.GET.get("audience", "")
        owner_filter = self.request.GET.get("owner", "")
        if query:
            contacts = contacts.filter(
                Q(contact_name__icontains=query)
                | Q(contact_email__icontains=query)
                | Q(contact_phone__icontains=query)
                | Q(organization_name__icontains=query)
                | Q(school_name__icontains=query)
            )
        if status_filter in Lead.Status.values:
            contacts = contacts.filter(status=status_filter)
        if audience_filter in Lead.Audience.values:
            contacts = contacts.filter(audience=audience_filter)
        if owner_filter == "unassigned":
            contacts = contacts.filter(assigned_to__isnull=True)
        elif owner_filter.isdigit():
            contacts = contacts.filter(assigned_to_id=owner_filter)

        ordering = self.request.GET.get("sort", "recent")
        order_by = {
            "name": ("contact_name", "contact_email"),
            "oldest": ("created_at",),
            "recent": (F("last_submission_at").desc(nulls_last=True), "-created_at"),
        }.get(ordering, (F("last_submission_at").desc(nulls_last=True), "-created_at"))
        contacts = contacts.order_by(*order_by)
        paginator = Paginator(contacts, 50)
        page_obj = paginator.get_page(self.request.GET.get("page"))
        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        now = timezone.now()
        context.update(
            {
                "contacts": page_obj.object_list,
                "page_obj": page_obj,
                "filter_query": query_params.urlencode(),
                "total_contacts": all_contacts.count(),
                "new_contacts": all_contacts.filter(status=Lead.Status.NEW).count(),
                "unassigned_contacts": all_contacts.filter(assigned_to__isnull=True).count(),
                "recent_submissions": FormSubmission.objects.filter(
                    created_at__gte=now - timezone.timedelta(days=30)
                ).count(),
                "overdue_tasks": CrmActivity.objects.filter(
                    activity_type=CrmActivity.ActivityType.TASK,
                    completed_at__isnull=True,
                    due_at__lt=now,
                ).count(),
                "owners": crm_owner_queryset(),
                "status_choices": Lead.Status.choices,
                "audience_choices": Lead.Audience.choices,
                "active_filters": {
                    "q": query,
                    "status": status_filter,
                    "audience": audience_filter,
                    "owner": owner_filter,
                    "sort": ordering,
                },
            }
        )
        return context


class CrmContactDetailView(CrmAccessMixin, TemplateView):
    template_name = "crm/contact_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lead = get_object_or_404(
            Lead.objects.filter(is_deleted=False).select_related("assigned_to", "linked_user"),
            pk=kwargs["pk"],
        )
        submissions = list(lead.form_submissions.all())
        activities = list(
            lead.crm_activities.select_related("created_by", "assigned_to").all()
        )
        timeline = [
            {"kind": "submission", "created_at": item.created_at, "item": item}
            for item in submissions
        ] + [
            {"kind": item.activity_type, "created_at": item.created_at, "item": item}
            for item in activities
        ]
        timeline.sort(key=lambda event: event["created_at"], reverse=True)
        context.update(
            {
                "lead": lead,
                "timeline": timeline,
                "open_tasks": [
                    activity
                    for activity in activities
                    if activity.activity_type == CrmActivity.ActivityType.TASK
                    and activity.completed_at is None
                ],
                "owners": crm_owner_queryset(),
                "status_choices": Lead.Status.choices,
                "audience_choices": Lead.Audience.choices,
            }
        )
        return context


class CrmContactUpdateView(CrmAccessMixin, View):
    def post(self, request, pk):
        lead = get_object_or_404(Lead, pk=pk, is_deleted=False)
        status_value = request.POST.get("status", "")
        audience = request.POST.get("audience", "")
        owner_value = request.POST.get("assigned_to", "")
        if status_value not in Lead.Status.values or audience not in Lead.Audience.values:
            messages.error(request, "Choose a valid status and audience.")
            return redirect("crm_contact_detail", pk=lead.pk)

        owner = None
        if owner_value:
            owner = crm_owner_queryset().filter(pk=owner_value).first()
            if owner is None:
                messages.error(request, "Choose a valid contact owner.")
                return redirect("crm_contact_detail", pk=lead.pk)

        before = {
            "status": lead.status,
            "audience": lead.audience,
            "assigned_to_id": lead.assigned_to_id,
        }
        lead.status = status_value
        lead.audience = audience
        lead.assigned_to = owner
        lead.save(update_fields=["status", "audience", "assigned_to", "updated_at"])
        AuditLog.objects.create(
            actor=request.user,
            action="crm.contact.updated",
            entity_type="Lead",
            entity_id=str(lead.pk),
            before=before,
            after={
                "status": lead.status,
                "audience": lead.audience,
                "assigned_to_id": lead.assigned_to_id,
            },
        )
        messages.success(request, "Contact properties updated.")
        return redirect("crm_contact_detail", pk=lead.pk)


class CrmNoteCreateView(CrmAccessMixin, View):
    def post(self, request, pk):
        lead = get_object_or_404(Lead, pk=pk, is_deleted=False)
        body = request.POST.get("body", "").strip()
        if not body:
            messages.error(request, "Enter a note before saving.")
            return redirect("crm_contact_detail", pk=lead.pk)
        activity = CrmActivity(
            lead=lead,
            activity_type=CrmActivity.ActivityType.NOTE,
            body=body,
            created_by=request.user,
        )
        activity.full_clean()
        activity.save()
        messages.success(request, "Note added to the activity timeline.")
        return redirect(f"{reverse('crm_contact_detail', args=[lead.pk])}#activity")


class CrmTaskCreateView(CrmAccessMixin, View):
    def post(self, request, pk):
        lead = get_object_or_404(Lead, pk=pk, is_deleted=False)
        subject = request.POST.get("subject", "").strip()[:255]
        due_at = parse_datetime(request.POST.get("due_at", ""))
        assigned_to = crm_owner_queryset().filter(
            pk=request.POST.get("assigned_to") or request.user.pk
        ).first()
        if not subject or due_at is None or assigned_to is None:
            messages.error(request, "Add a task title, due date, and valid owner.")
            return redirect("crm_contact_detail", pk=lead.pk)
        if timezone.is_naive(due_at):
            due_at = timezone.make_aware(due_at)
        activity = CrmActivity(
            lead=lead,
            activity_type=CrmActivity.ActivityType.TASK,
            subject=subject,
            body=request.POST.get("body", "").strip(),
            due_at=due_at,
            created_by=request.user,
            assigned_to=assigned_to,
        )
        activity.full_clean()
        activity.save()
        messages.success(request, "Follow-up task created.")
        return redirect(f"{reverse('crm_contact_detail', args=[lead.pk])}#activity")


class CrmTaskCompleteView(CrmAccessMixin, View):
    def post(self, request, pk, activity_id):
        activity = get_object_or_404(
            CrmActivity,
            pk=activity_id,
            lead_id=pk,
            activity_type=CrmActivity.ActivityType.TASK,
        )
        if activity.completed_at is None:
            activity.completed_at = timezone.now()
            activity.save(update_fields=["completed_at", "updated_at"])
        messages.success(request, "Task marked complete.")
        return redirect(f"{reverse('crm_contact_detail', args=[pk])}#activity")

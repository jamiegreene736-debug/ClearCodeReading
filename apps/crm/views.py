from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count, F, Max, OuterRef, Q, Subquery, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from apps.core.models import RecruitingInterest
from apps.crm.models import Company, CrmActivity, FormSubmission, IntakeTriage, Lead, NewsletterSubscription, Opportunity
from apps.crm.newsletters import resolve_unsubscribe_token
from apps.crm.serializers import CompanySerializer, LeadSerializer, OpportunitySerializer
from apps.crm.services import (
    LeadIntake,
    TERMINAL_DEAL_STAGES,
    create_partner_triage,
    ensure_family_enrollment_deal,
    partner_interest_is_selected,
    record_form_submission,
    resolve_triage_item,
    sanitized_submission_data,
)
from apps.schools.models import School
from apps.users.models import AuditLog, CustomUser


class WebsiteSignupView(View):
    def post(self, request):
        contact_name = request.POST.get("name", "").strip()[:255]
        contact_email = request.POST.get("email", "").strip().lower()
        career_path = request.POST.get("career_path", "").strip()
        role_interest = request.POST.get("role_interest", "").strip()[:255]
        submitted_notes = request.POST.get("notes", "").strip()
        is_career_signup = request.POST.get("redirect_to") == "/careers/"
        career_fields_are_missing = is_career_signup and (
            career_path not in RecruitingInterest.CareerPath.values or not role_interest or not submitted_notes
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

        if is_career_signup:
            RecruitingInterest.objects.create(
                name=contact_name,
                email=contact_email,
                phone=request.POST.get("phone", "").strip()[:32],
                career_path=career_path,
                role_interest=role_interest,
                notes=submitted_notes,
                source_path="/careers/",
            )
            messages.success(request, "Thanks. Your interest is with the ClearCode recruiting team.")
            return redirect(self._redirect_target(request, "thanks"))

        audience = request.POST.get("audience", Lead.Audience.PARENT)
        if audience not in Lead.Audience.values:
            audience = Lead.Audience.OTHER

        organization_name = request.POST.get("organization_name", "").strip()[:255]
        contact_phone = request.POST.get("phone", "").strip()[:32]
        notes = submitted_notes
        child_age_grade = request.POST.get("child_age_grade", "").strip()
        if child_age_grade:
            notes = "\n".join(part for part in [f"Child age or grade: {child_age_grade}", notes] if part)
        estimated_students = self._clean_positive_int(request.POST.get("estimated_students"))
        school_name = self._school_name_for_signup(audience, organization_name)
        source_path = self._source_path(request, organization_name)
        lead, submission = record_form_submission(
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
                    "partner_interest": partner_interest_is_selected(request.POST.get("partner_interest")),
                },
            ),
            form_type=self._form_type(request, organization_name),
            source_path=source_path,
            submitted_data=sanitized_submission_data(request.POST),
        )
        form_type = submission.form_type
        if form_type in {FormSubmission.FormType.CONSULTATION, FormSubmission.FormType.ASSESSMENT}:
            ensure_family_enrollment_deal(lead=lead)
        if partner_interest_is_selected(request.POST.get("partner_interest")):
            create_partner_triage(lead=lead, submission=submission)

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
        pipeline = request.data.get("pipeline", Opportunity.Pipeline.FAMILY_ENROLLMENT)
        if pipeline not in Opportunity.Pipeline.values:
            return Response({"pipeline": "Invalid CRM pipeline."}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            opportunity = Opportunity(
                lead=lead,
                company=lead.company,
                owner=request.user,
                name=request.data.get("name", f"{lead.school_name} Deal"),
                pipeline=pipeline,
                stage=Opportunity.initial_stage_for_pipeline(pipeline),
                value=request.data.get("value", 0),
                probability=request.data.get("probability", 10),
                expected_close_date=request.data.get("expected_close_date") or None,
                metadata={"converted_from_lead_id": lead.id},
            )
            opportunity.full_clean()
            opportunity.save()
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


class CompanyViewSet(viewsets.ModelViewSet):
    serializer_class = CompanySerializer
    permission_classes = [IsAuthenticated, IsCrmAdmin]

    def get_queryset(self):
        queryset = Company.objects.filter(is_deleted=False).select_related("owner")
        owner = self.request.query_params.get("owner")
        if owner:
            queryset = queryset.filter(owner_id=owner)
        return queryset

    def perform_create(self, serializer):
        serializer.save(owner=serializer.validated_data.get("owner") or self.request.user)


class OpportunityViewSet(viewsets.ModelViewSet):
    serializer_class = OpportunitySerializer
    permission_classes = [IsAuthenticated, IsCrmAdmin]

    def get_queryset(self):
        queryset = Opportunity.objects.filter(is_deleted=False).select_related("lead", "company", "school", "owner")
        pipeline = self.request.query_params.get("pipeline")
        stage = self.request.query_params.get("stage")
        owner = self.request.query_params.get("owner")
        if pipeline:
            queryset = queryset.filter(pipeline=pipeline)
        if stage:
            queryset = queryset.filter(stage=stage)
        if owner:
            queryset = queryset.filter(owner_id=owner)
        return queryset

    @action(detail=True, methods=["post"])
    def advance(self, request, pk=None):
        opportunity = self.get_object()
        stage = request.data.get("stage")
        if stage not in Opportunity.stage_values_for_pipeline(opportunity.pipeline):
            return Response({"stage": "Choose a stage in this deal's pipeline."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            probability = int(request.data.get("probability", opportunity.probability))
        except (TypeError, ValueError):
            return Response({"probability": "Enter a whole number from 0 to 100."}, status=status.HTTP_400_BAD_REQUEST)
        if not 0 <= probability <= 100:
            return Response({"probability": "Enter a whole number from 0 to 100."}, status=status.HTTP_400_BAD_REQUEST)
        opportunity.stage = stage
        opportunity.probability = probability
        opportunity.next_steps = request.data.get("next_steps", opportunity.next_steps)
        opportunity.closed_at = timezone.now() if stage in TERMINAL_DEAL_STAGES else None
        if stage in {Opportunity.Stage.LOST, Opportunity.Stage.DECLINED, Opportunity.Stage.PASSED}:
            opportunity.lost_reason = request.data.get("lost_reason", opportunity.lost_reason)
        school_id = request.data.get("school")
        if school_id:
            opportunity.school = School.objects.get(id=school_id)
        opportunity.full_clean()
        opportunity.save()
        return Response(OpportunitySerializer(opportunity, context=self.get_serializer_context()).data)


def crm_owner_queryset():
    return CustomUser.objects.filter(is_active=True, is_deleted=False).filter(
        Q(is_superuser=True) | Q(is_staff=True) | Q(role=CustomUser.Role.SUPER_ADMIN)
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


class CrmCompanyListView(CrmAccessMixin, TemplateView):
    template_name = "crm/company_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()[:255]
        companies = Company.objects.filter(is_deleted=False).select_related("owner")
        if query:
            companies = companies.filter(
                Q(name__icontains=query)
                | Q(website__icontains=query)
                | Q(contacts__contact_name__icontains=query)
                | Q(contacts__contact_email__icontains=query)
            )
        deal_totals = (
            Opportunity.objects.filter(company_id=OuterRef("pk"), is_deleted=False)
            .values("company_id")
            .annotate(total=Sum("value"))
            .values("total")
        )
        companies = companies.annotate(
            contact_count=Count("contacts", filter=Q(contacts__is_deleted=False), distinct=True),
            deal_count=Count("deals", filter=Q(deals__is_deleted=False), distinct=True),
            total_deal_value=Subquery(deal_totals),
        ).order_by("name").distinct()
        context.update({"companies": companies, "query": query})
        return context


class CrmCompanyDetailView(CrmAccessMixin, TemplateView):
    template_name = "crm/company_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = get_object_or_404(
            Company.objects.filter(is_deleted=False).select_related("owner"),
            pk=kwargs["pk"],
        )
        context.update(
            {
                "company": company,
                "contacts": company.contacts.filter(is_deleted=False).select_related("assigned_to"),
                "deals": company.deals.filter(is_deleted=False).select_related("lead", "owner"),
            }
        )
        return context


class CrmDealListView(CrmAccessMixin, TemplateView):
    template_name = "crm/deal_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pipeline = self.request.GET.get("pipeline", Opportunity.Pipeline.FAMILY_ENROLLMENT)
        if pipeline not in Opportunity.Pipeline.values:
            pipeline = Opportunity.Pipeline.FAMILY_ENROLLMENT
        deals = list(
            Opportunity.objects.filter(is_deleted=False, pipeline=pipeline)
            .select_related("lead", "company", "owner")
            .prefetch_related("related_deals")
            .order_by("expected_close_date", "-created_at")
        )
        stage_columns = []
        for stage, label in Opportunity.stage_choices_for_pipeline(pipeline):
            stage_columns.append(
                {
                    "stage": stage,
                    "label": label,
                    "deals": [deal for deal in deals if deal.stage == stage],
                }
            )
        context.update(
            {
                "pipeline": pipeline,
                "pipeline_label": Opportunity.Pipeline(pipeline).label,
                "pipeline_choices": Opportunity.Pipeline.choices,
                "stage_columns": stage_columns,
                "deal_count": len(deals),
                "pipeline_value": sum(deal.value for deal in deals),
            }
        )
        return context


class CrmDealCreateView(CrmAccessMixin, View):
    def post(self, request, pk):
        lead = get_object_or_404(Lead.objects.select_related("company"), pk=pk, is_deleted=False)
        pipeline = request.POST.get("pipeline", "")
        name = request.POST.get("name", "").strip()[:255]
        if pipeline not in Opportunity.Pipeline.values or not name:
            messages.error(request, "Add a deal name and choose a valid pipeline.")
            return redirect("crm_contact_detail", pk=lead.pk)

        company = lead.company
        company_id = request.POST.get("company", "")
        company_name = request.POST.get("company_name", "").strip()[:255]
        if company_id:
            company = Company.objects.filter(pk=company_id, is_deleted=False).first()
            if company is None:
                messages.error(request, "Choose a valid company.")
                return redirect("crm_contact_detail", pk=lead.pk)
        elif company_name:
            company, _created = Company.objects.get_or_create(
                name__iexact=company_name,
                is_deleted=False,
                defaults={"name": company_name, "owner": request.user},
            )

        if company and lead.company_id not in {None, company.pk}:
            messages.error(request, "This contact already belongs to a different company.")
            return redirect("crm_contact_detail", pk=lead.pk)
        if company and lead.company_id is None:
            lead.company = company
            lead.save(update_fields=["company", "updated_at"])

        deal = Opportunity(
            lead=lead,
            company=company,
            owner=request.user,
            name=name,
            pipeline=pipeline,
            stage=Opportunity.initial_stage_for_pipeline(pipeline),
            value=request.POST.get("value") or 0,
            probability=10,
            expected_close_date=parse_date(request.POST.get("expected_close_date", "")),
        )
        try:
            deal.full_clean()
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("crm_contact_detail", pk=lead.pk)
        deal.save()

        related_deal_id = request.POST.get("related_deal", "")
        if related_deal_id:
            related = Opportunity.objects.filter(pk=related_deal_id, is_deleted=False).first()
            if related and (
                related.lead_id == lead.pk
                or (company and related.company_id == company.pk)
            ):
                deal.related_deals.add(related)
        AuditLog.objects.create(
            actor=request.user,
            action="crm.deal.created",
            entity_type="Opportunity",
            entity_id=str(deal.pk),
            after={"lead_id": lead.pk, "company_id": company.pk if company else None, "pipeline": pipeline},
        )
        messages.success(request, "Deal created in the selected pipeline.")
        return redirect(f"{reverse('crm_deal_list')}?pipeline={pipeline}")


class CrmDealStageUpdateView(CrmAccessMixin, View):
    def post(self, request, pk):
        deal = get_object_or_404(Opportunity, pk=pk, is_deleted=False)
        stage = request.POST.get("stage", "")
        if stage not in Opportunity.stage_values_for_pipeline(deal.pipeline):
            messages.error(request, "Choose a stage in this deal's pipeline.")
            return redirect(f"{reverse('crm_deal_list')}?pipeline={deal.pipeline}")
        previous_stage = deal.stage
        deal.stage = stage
        deal.closed_at = timezone.now() if stage in TERMINAL_DEAL_STAGES else None
        deal.full_clean()
        deal.save(update_fields=["stage", "closed_at", "updated_at"])
        AuditLog.objects.create(
            actor=request.user,
            action="crm.deal.stage_updated",
            entity_type="Opportunity",
            entity_id=str(deal.pk),
            before={"stage": previous_stage},
            after={"stage": stage, "pipeline": deal.pipeline},
        )
        messages.success(request, "Deal stage updated.")
        return redirect(f"{reverse('crm_deal_list')}?pipeline={deal.pipeline}")


class CrmTriageListView(CrmAccessMixin, TemplateView):
    template_name = "crm/triage_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        items = (
            IntakeTriage.objects.filter(status=IntakeTriage.Status.PENDING)
            .select_related("lead__company", "submission")
            .order_by("created_at")
        )
        context.update({"triage_items": items, "pipeline_choices": Opportunity.Pipeline.choices})
        return context


class CrmTriageResolveView(CrmAccessMixin, View):
    def post(self, request, pk):
        triage = get_object_or_404(IntakeTriage, pk=pk)
        dismiss = request.POST.get("action") == "dismiss"
        pipelines = request.POST.getlist("pipelines")
        try:
            resolved = resolve_triage_item(
                triage=triage,
                pipelines=pipelines,
                actor=request.user,
                notes=request.POST.get("resolution_notes", ""),
                dismiss=dismiss,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("crm_triage_list")
        AuditLog.objects.create(
            actor=request.user,
            action="crm.triage.dismissed" if dismiss else "crm.triage.resolved",
            entity_type="IntakeTriage",
            entity_id=str(resolved.pk),
            after={"pipelines": resolved.selected_pipelines},
        )
        messages.success(request, "Triage item dismissed." if dismiss else "Triage complete and selected deal records are ready.")
        return redirect("crm_triage_list")


class CrmContactListView(CrmAccessMixin, TemplateView):
    template_name = "crm/contact_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_contacts = Lead.objects.filter(is_deleted=False)
        contacts = all_contacts.select_related("assigned_to", "company").annotate(
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
                "pending_triage": IntakeTriage.objects.filter(status=IntakeTriage.Status.PENDING).count(),
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
            Lead.objects.filter(is_deleted=False).select_related("assigned_to", "linked_user", "company"),
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
                "companies": Company.objects.filter(is_deleted=False).order_by("name"),
                "deals": lead.opportunities.filter(is_deleted=False).select_related("company", "owner").prefetch_related("related_deals"),
                "pipeline_choices": Opportunity.Pipeline.choices,
                "related_deal_choices": Opportunity.objects.filter(is_deleted=False).select_related("company", "lead").order_by("company__name", "name"),
                "pending_triage_count": lead.triage_items.filter(status=IntakeTriage.Status.PENDING).count(),
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
        company_value = request.POST.get("company", "")
        company_name = request.POST.get("company_name", "").strip()[:255]
        if status_value not in Lead.Status.values or audience not in Lead.Audience.values:
            messages.error(request, "Choose a valid status and audience.")
            return redirect("crm_contact_detail", pk=lead.pk)

        owner = None
        if owner_value:
            owner = crm_owner_queryset().filter(pk=owner_value).first()
            if owner is None:
                messages.error(request, "Choose a valid contact owner.")
                return redirect("crm_contact_detail", pk=lead.pk)

        company = None
        if company_value:
            company = Company.objects.filter(pk=company_value, is_deleted=False).first()
            if company is None:
                messages.error(request, "Choose a valid company.")
                return redirect("crm_contact_detail", pk=lead.pk)
        elif company_name:
            company, _created = Company.objects.get_or_create(
                name__iexact=company_name,
                is_deleted=False,
                defaults={"name": company_name, "owner": request.user},
            )

        before = {
            "status": lead.status,
            "audience": lead.audience,
            "assigned_to_id": lead.assigned_to_id,
            "company_id": lead.company_id,
        }
        lead.status = status_value
        lead.audience = audience
        lead.assigned_to = owner
        old_company_id = lead.company_id
        lead.company = company
        with transaction.atomic():
            lead.save(update_fields=["status", "audience", "assigned_to", "company", "updated_at"])
            lead.opportunities.filter(
                Q(company_id=old_company_id) | Q(company__isnull=True)
            ).update(company=company, updated_at=timezone.now())
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
                "company_id": lead.company_id,
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

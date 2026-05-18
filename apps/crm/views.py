from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.utils import timezone
from django.views import View
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import IsSchoolAdmin
from apps.crm.models import Lead, Opportunity
from apps.crm.serializers import LeadSerializer, OpportunitySerializer
from apps.schools.models import School
from apps.users.models import AuditLog, CustomUser


class WebsiteSignupView(View):
    def post(self, request):
        contact_name = request.POST.get("name", "").strip()
        contact_email = request.POST.get("email", "").strip().lower()
        audience = request.POST.get("audience", Lead.Audience.PARENT)
        if audience not in Lead.Audience.values:
            audience = Lead.Audience.OTHER

        if not contact_name or not contact_email:
            messages.error(request, "Please add your name and email so we can follow up.")
            return redirect("/?signup=missing#signup")

        organization_name = request.POST.get("organization_name", "").strip()
        contact_phone = request.POST.get("phone", "").strip()
        notes = request.POST.get("notes", "").strip()
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

        messages.success(request, "Thanks. We saved your request and the Clear Code Reading team can see it in the admin portal.")
        return redirect("/?signup=thanks#top")

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

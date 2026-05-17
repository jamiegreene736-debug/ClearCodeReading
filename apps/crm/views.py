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

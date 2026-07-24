from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import IsEvaluator
from apps.scheduling.integrations import SchedulerError, SchedulerNotConfigured, get_scheduler_adapter
from apps.scheduling.models import ProviderAvailability, ScheduleBooking, ScheduleGroupProposal, WaitlistEntry
from apps.scheduling.optimizer import (
    ProposalConflict,
    approve_group_proposal,
    generate_group_proposals,
    reject_group_proposal,
)
from apps.scheduling.serializers import (
    GenerateProposalsSerializer,
    ProviderAvailabilitySerializer,
    ScheduleBookingSerializer,
    ScheduleGroupProposalSerializer,
    WaitlistEntrySerializer,
)
from apps.scheduling.services import (
    operations_metrics,
    ranked_group_suggestions,
    reconcile_remote_bookings,
    sync_booking,
)
from apps.schools.models import School, SchoolMembership
from apps.users.models import CustomUser


OPS_ROLES = [SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN]
SCHEDULING_ROLES = OPS_ROLES + [SchoolMembership.Role.SPECIALIST]


def _center_ids(user):
    if user.is_superuser or user.role == CustomUser.Role.SUPER_ADMIN:
        return School.objects.filter(is_deleted=False).values_list("id", flat=True)
    return user.school_memberships.filter(is_deleted=False).values_list("school_id", flat=True)


def _has_center_role(user, center, roles):
    if user.is_superuser or user.role == CustomUser.Role.SUPER_ADMIN:
        return True
    return user.school_memberships.filter(school=center, role__in=roles, is_deleted=False).exists()


def _accessible_center(user, center_id, roles=None):
    try:
        center = School.objects.filter(pk=center_id, pk__in=_center_ids(user), is_deleted=False).first()
    except (TypeError, ValueError):
        return None
    if center is None or (roles and not _has_center_role(user, center, roles)):
        return None
    return center


class CenterScopedMutationMixin:
    def _assert_center(self, serializer):
        center = serializer.validated_data.get("center") or getattr(serializer.instance, "center", None)
        if center.id not in set(_center_ids(self.request.user)):
            raise PermissionDenied("You cannot modify scheduling records for another center.")

    def perform_create(self, serializer):
        self._assert_center(serializer)
        serializer.save()

    def perform_update(self, serializer):
        self._assert_center(serializer)
        serializer.save()


class ProviderAvailabilityViewSet(CenterScopedMutationMixin, viewsets.ModelViewSet):
    serializer_class = ProviderAvailabilitySerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        return ProviderAvailability.objects.filter(center_id__in=_center_ids(self.request.user)).select_related(
            "center", "specialist"
        )


class WaitlistEntryViewSet(CenterScopedMutationMixin, viewsets.ModelViewSet):
    serializer_class = WaitlistEntrySerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        return WaitlistEntry.objects.filter(center_id__in=_center_ids(self.request.user)).select_related(
            "center", "child"
        )


class ScheduleGroupProposalViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ScheduleGroupProposalSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            ScheduleGroupProposal.objects.filter(center_id__in=_center_ids(self.request.user))
            .select_related("center", "specialist", "curriculum", "created_by", "reviewed_by")
            .prefetch_related("children", "bookings__child", "bookings__specialist")
        )

    @action(detail=False, methods=["post"])
    def generate(self, request):
        input_serializer = GenerateProposalsSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data
        center = _accessible_center(request.user, data["center"], SCHEDULING_ROLES)
        if center is None:
            raise PermissionDenied("Scheduling staff access to this center is required.")
        specialist = None
        if specialist_id := data.get("specialist"):
            specialist = (
                CustomUser.objects.filter(
                    pk=specialist_id,
                    school_memberships__school=center,
                    school_memberships__role=SchoolMembership.Role.SPECIALIST,
                    school_memberships__is_deleted=False,
                    is_active=True,
                    is_deleted=False,
                )
                .distinct()
                .first()
            )
            if specialist is None:
                return Response(
                    {"specialist": "An active specialist at this center is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        try:
            result = generate_group_proposals(
                center=center,
                start_date=data["start_date"],
                end_date=data["end_date"],
                specialist=specialist,
                created_by=request.user,
                max_position_gap=data["max_position_gap"],
                session_minutes=data["session_minutes"],
                limit=data["limit"],
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        proposals = self.get_queryset().filter(pk__in=[proposal.pk for proposal in result["proposals"]])
        return Response(
            {
                "center": center.id,
                "advisory": True,
                "approval_required": True,
                "created_count": proposals.count(),
                "proposals": self.get_serializer(proposals, many=True).data,
                "excluded_pending_consent": result["excluded_pending_consent"],
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        proposal = self.get_object()
        if not _has_center_role(request.user, proposal.center, OPS_ROLES):
            raise PermissionDenied("Center operations leadership must approve proposals.")
        try:
            proposal = approve_group_proposal(proposal, request.user)
        except ProposalConflict as error:
            return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(proposal).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        proposal = self.get_object()
        if not _has_center_role(request.user, proposal.center, OPS_ROLES):
            raise PermissionDenied("Center operations leadership must reject proposals.")
        try:
            proposal = reject_group_proposal(proposal, request.user)
        except ProposalConflict as error:
            return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(proposal).data)


class ScheduleBookingViewSet(CenterScopedMutationMixin, viewsets.ModelViewSet):
    serializer_class = ScheduleBookingSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        return ScheduleBooking.objects.filter(center_id__in=_center_ids(self.request.user)).select_related(
            "center", "proposal", "child", "specialist", "approved_by"
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        booking = self.get_object()
        if not _has_center_role(request.user, booking.center, OPS_ROLES):
            raise PermissionDenied("Center operations leadership must approve bookings.")
        if booking.proposal_id:
            return Response(
                {"detail": "Approve the parent group proposal so the group remains atomic."},
                status=status.HTTP_409_CONFLICT,
            )
        if not booking.child.idea_services_authorized:
            return Response(
                {"detail": "IEP authorization is pending; approval is blocked."},
                status=status.HTTP_409_CONFLICT,
            )
        booking.status = ScheduleBooking.Status.APPROVED
        booking.approved_by = request.user
        booking.approved_at = timezone.now()
        booking.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        return Response(self.get_serializer(booking).data)

    def _sync_response(self, booking):
        if booking.status not in [ScheduleBooking.Status.APPROVED, ScheduleBooking.Status.CONFIRMED]:
            return Response(
                {"detail": "Staff approval is required before scheduler sync."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            adapter = get_scheduler_adapter()
        except SchedulerNotConfigured as error:
            return Response({"detail": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            booking = sync_booking(booking, adapter)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)
        response_status = (
            status.HTTP_502_BAD_GATEWAY
            if booking.sync_status == ScheduleBooking.SyncStatus.ERROR
            else status.HTTP_200_OK
        )
        return Response(self.get_serializer(booking).data, status=response_status)

    @action(detail=True, methods=["post"])
    def sync(self, request, pk=None):
        return self._sync_response(self.get_object())

    @action(detail=True, methods=["post"], url_path="force-sync")
    def force_sync(self, request, pk=None):
        return self._sync_response(self.get_object())

    @action(detail=False, methods=["post"], url_path="reconcile-inbound")
    def reconcile_inbound(self, request):
        center = _accessible_center(request.user, request.data.get("center"), OPS_ROLES)
        if center is None:
            raise PermissionDenied("Center operations access is required.")
        try:
            adapter = get_scheduler_adapter()
            result = reconcile_remote_bookings(
                center=center,
                adapter=adapter,
                start_date=parse_date(request.data.get("start_date") or ""),
                end_date=parse_date(request.data.get("end_date") or ""),
                updated_since=parse_datetime(request.data.get("updated_since") or ""),
            )
        except SchedulerNotConfigured as error:
            return Response({"detail": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except SchedulerError as error:
            return Response({"detail": str(error)}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception:
            return Response(
                {"detail": "Unexpected scheduler adapter failure; no local booking identity was changed."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(result)

    @action(detail=False, methods=["get"], url_path="recommendations")
    def recommendations(self, request):
        center = _accessible_center(request.user, request.query_params.get("center"), SCHEDULING_ROLES)
        if center is None:
            raise PermissionDenied("Scheduling staff access to this center is required.")
        return Response({"center": center.id, "suggestions": ranked_group_suggestions(center)})

    @action(detail=False, methods=["get"], url_path="operations-metrics")
    def operations_metrics_action(self, request):
        center = _accessible_center(request.user, request.query_params.get("center"), OPS_ROLES)
        if center is None:
            raise PermissionDenied("Center operations access is required.")
        start = parse_datetime(request.query_params.get("start", ""))
        end = parse_datetime(request.query_params.get("end", ""))
        if bool(start) != bool(end):
            return Response(
                {"detail": "Provide both ISO-8601 start and end, or neither."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            metrics = operations_metrics(center, start=start, end=end)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"center": center.id, **metrics})

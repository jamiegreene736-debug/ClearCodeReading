from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.permissions import IsEvaluator
from apps.scheduling.integrations import SchedulerNotConfigured, get_scheduler_adapter
from apps.scheduling.models import ProviderAvailability, ScheduleBooking, WaitlistEntry
from apps.scheduling.serializers import ProviderAvailabilitySerializer, ScheduleBookingSerializer, WaitlistEntrySerializer
from apps.scheduling.services import operations_metrics, ranked_group_suggestions
from apps.schools.models import School
from apps.users.models import ChildProfile, CustomUser


def _center_ids(user):
    if user.is_superuser or user.role == CustomUser.Role.SUPER_ADMIN:
        return School.objects.filter(is_deleted=False).values_list("id", flat=True)
    return user.school_memberships.filter(is_deleted=False).values_list("school_id", flat=True)


class CenterScopedMutationMixin:
    def _assert_center(self, serializer):
        center = serializer.validated_data.get("center") or getattr(serializer.instance, "center", None)
        if center.id not in set(_center_ids(self.request.user)):
            from rest_framework.exceptions import PermissionDenied
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
        return ProviderAvailability.objects.filter(center_id__in=_center_ids(self.request.user)).select_related("center", "specialist")


class WaitlistEntryViewSet(CenterScopedMutationMixin, viewsets.ModelViewSet):
    serializer_class = WaitlistEntrySerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        return WaitlistEntry.objects.filter(center_id__in=_center_ids(self.request.user)).select_related("center", "child")


class ScheduleBookingViewSet(CenterScopedMutationMixin, viewsets.ModelViewSet):
    serializer_class = ScheduleBookingSerializer
    permission_classes = [IsAuthenticated, IsEvaluator]

    def get_queryset(self):
        return ScheduleBooking.objects.filter(center_id__in=_center_ids(self.request.user)).select_related("center", "child", "specialist", "approved_by")

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        booking = self.get_object()
        booking.status = ScheduleBooking.Status.APPROVED
        booking.approved_by = request.user
        booking.approved_at = timezone.now()
        booking.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        return Response(self.get_serializer(booking).data)

    @action(detail=True, methods=["post"])
    def sync(self, request, pk=None):
        booking = self.get_object()
        if booking.status not in [ScheduleBooking.Status.APPROVED, ScheduleBooking.Status.CONFIRMED]:
            return Response({"detail": "Staff approval is required before scheduler sync."}, status=status.HTTP_409_CONFLICT)
        try:
            adapter = get_scheduler_adapter()
            external_id = adapter.upsert_booking(booking)
        except SchedulerNotConfigured as error:
            return Response({"detail": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        booking.scheduler_provider = adapter.provider
        booking.external_booking_id = external_id
        booking.sync_status = ScheduleBooking.SyncStatus.SYNCED
        booking.status = ScheduleBooking.Status.CONFIRMED
        booking.save(update_fields=["scheduler_provider", "external_booking_id", "sync_status", "status", "updated_at"])
        return Response(self.get_serializer(booking).data)

    @action(detail=False, methods=["post"], url_path="reconcile-inbound")
    def reconcile_inbound(self, request):
        center = School.objects.filter(pk=request.data.get("center"), pk__in=_center_ids(request.user)).first()
        if center is None:
            return Response({"center": "A center you can access is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            adapter = get_scheduler_adapter()
            remote_bookings = adapter.pull_bookings(request.data.get("updated_since"))
        except SchedulerNotConfigured as error:
            return Response({"detail": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        reconciled = []
        skipped = []
        for remote in remote_bookings:
            external_id = remote.get("external_booking_id")
            if not external_id:
                skipped.append({"reason": "missing_external_booking_id"})
                continue
            child = ChildProfile.objects.filter(pk=remote.get("child_id"), school=center, is_deleted=False).first()
            specialist = CustomUser.objects.filter(pk=remote.get("specialist_id"), school_memberships__school=center, school_memberships__is_deleted=False).distinct().first()
            starts_at = parse_datetime(remote.get("starts_at", ""))
            ends_at = parse_datetime(remote.get("ends_at", ""))
            if child is None or specialist is None or starts_at is None or ends_at is None or ends_at <= starts_at:
                skipped.append({"external_booking_id": external_id, "reason": "invalid_or_cross_center_payload"})
                continue
            if not child.idea_services_authorized:
                skipped.append({"external_booking_id": external_id, "reason": "iep_authorization_pending"})
                continue
            defaults = {
                "center": center,
                "child": child,
                "specialist": specialist,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "status": remote.get("status", ScheduleBooking.Status.CONFIRMED),
                "scheduler_provider": adapter.provider,
                "sync_status": ScheduleBooking.SyncStatus.SYNCED,
                "metadata": {"remote": remote},
            }
            booking, _ = ScheduleBooking.objects.update_or_create(
                center=center,
                external_booking_id=external_id,
                defaults=defaults,
            )
            reconciled.append(booking.id)
        return Response({"provider": adapter.provider, "reconciled": len(reconciled), "booking_ids": reconciled, "skipped": skipped})

    @action(detail=False, methods=["get"], url_path="recommendations")
    def recommendations(self, request):
        center = School.objects.filter(pk=request.query_params.get("center"), pk__in=_center_ids(request.user)).first()
        if center is None:
            return Response({"center": "A center you can access is required."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"center": center.id, "suggestions": ranked_group_suggestions(center)})

    @action(detail=False, methods=["get"], url_path="operations-metrics")
    def operations_metrics_action(self, request):
        center = School.objects.filter(pk=request.query_params.get("center"), pk__in=_center_ids(request.user)).first()
        if center is None:
            return Response({"center": "A center you can access is required."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"center": center.id, **operations_metrics(center)})

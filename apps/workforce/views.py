from __future__ import annotations

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied, ValidationError
from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.sessions.models import Session
from apps.workforce.access import has_workforce_role, is_global_admin, managed_center_ids
from apps.workforce.integrations import InvalidProviderWebhook, WorkforceProviderError
from apps.workforce.models import (
    Engagement,
    PayableItem,
    PaymentRun,
    RateSchedule,
    WorkerAssignment,
    WorkerProfile,
    WorkforceRoleMembership,
)
from apps.workforce.serializers import (
    AssignmentCreateSerializer,
    ClassificationReviewInputSerializer,
    ClassificationReviewSerializer,
    EngagementSerializer,
    PayableItemSerializer,
    PaymentRunPayablesSerializer,
    PaymentRunSerializer,
    ProviderOnboardingSerializer,
    ProviderWebhookResponseSerializer,
    RateScheduleSerializer,
    SessionPayableInputSerializer,
    WorkerProfileSerializer,
)
from apps.workforce.services import (
    add_payables_to_run,
    approve_payable,
    approve_payment_run,
    approve_rate,
    create_payable_from_session,
    create_provider_invite,
    payment_readiness,
    record_classification_review,
    record_provider_event,
    review_payment_run,
    submit_payment_run,
    sync_onboarding,
)


def _has_any_central_role(user) -> bool:
    return is_global_admin(user) or user.workforce_roles.filter(is_active=True).exists()


def _engagements_for(user):
    queryset = Engagement.objects.select_related("payer", "worker__user").prefetch_related(
        "assignments__center", "classification_reviews", "agreements", "credentials", "compliance_tasks"
    ).order_by("id")
    if is_global_admin(user):
        return queryset
    central_payers = user.workforce_roles.filter(is_active=True).values_list("payer_id", flat=True)
    return queryset.filter(
        Q(payer_id__in=central_payers)
        | Q(worker__user=user)
        | Q(assignments__center_id__in=managed_center_ids(user), assignments__is_active=True)
    ).distinct()


def _service_error(error):
    if hasattr(error, "message_dict"):
        detail = error.message_dict
    elif hasattr(error, "messages"):
        detail = error.messages
    else:
        detail = str(error)
    return Response({"detail": detail}, status=status.HTTP_409_CONFLICT)


class WorkerProfileViewSet(viewsets.ModelViewSet):
    serializer_class = WorkerProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if is_global_admin(self.request.user):
            return WorkerProfile.objects.select_related("user")
        if _has_any_central_role(self.request.user):
            payer_ids = self.request.user.workforce_roles.filter(is_active=True).values_list("payer_id", flat=True)
            return WorkerProfile.objects.filter(engagements__payer_id__in=payer_ids).select_related("user").distinct()
        return WorkerProfile.objects.filter(user=self.request.user).select_related("user")

    def perform_create(self, serializer):
        if not _has_any_central_role(self.request.user):
            raise PermissionDenied("ClearCode workforce administration access is required.")
        serializer.save()

    def perform_update(self, serializer):
        if not _has_any_central_role(self.request.user):
            raise PermissionDenied("ClearCode workforce administration access is required.")
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "Worker records are retained for compliance and cannot be deleted."}, status=405)


class EngagementViewSet(viewsets.ModelViewSet):
    serializer_class = EngagementSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return _engagements_for(self.request.user)

    def _require_admin(self, payer):
        if not has_workforce_role(self.request.user, payer, WorkforceRoleMembership.Role.WORKFORCE_ADMIN):
            raise PermissionDenied("ClearCode workforce administration access is required.")

    def perform_create(self, serializer):
        payer = serializer.validated_data["payer"]
        self._require_admin(payer)
        serializer.save()

    def perform_update(self, serializer):
        self._require_admin(serializer.instance.payer)
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "Engagements must be ended, not deleted."}, status=405)

    @action(detail=True, methods=["post"], url_path="classification-review")
    def classification_review(self, request, pk=None):
        engagement = self.get_object()
        input_serializer = ClassificationReviewInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            review = record_classification_review(
                engagement=engagement,
                reviewer=request.user,
                **input_serializer.validated_data,
            )
        except DjangoPermissionDenied as error:
            raise PermissionDenied(str(error)) from error
        except ValidationError as error:
            return _service_error(error)
        return Response(ClassificationReviewSerializer(review).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        engagement = self.get_object()
        self._require_admin(engagement.payer)
        input_serializer = AssignmentCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        assignment = WorkerAssignment(engagement=engagement, **input_serializer.validated_data)
        try:
            assignment.full_clean()
            assignment.save()
        except ValidationError as error:
            return _service_error(error)
        return Response(AssignmentCreateSerializer(assignment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="invite")
    def invite(self, request, pk=None):
        engagement = self.get_object()
        try:
            onboarding, invite_url = create_provider_invite(engagement=engagement, actor=request.user)
        except DjangoPermissionDenied as error:
            raise PermissionDenied(str(error)) from error
        except ValidationError as error:
            return _service_error(error)
        except WorkforceProviderError as error:
            return Response({"detail": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        data = ProviderOnboardingSerializer(onboarding).data
        data["invite_url"] = invite_url
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="sync-onboarding")
    def sync_provider_onboarding(self, request, pk=None):
        engagement = self.get_object()
        try:
            onboarding = sync_onboarding(onboarding=engagement.provider_onboarding, actor=request.user)
        except DjangoPermissionDenied as error:
            raise PermissionDenied(str(error)) from error
        except ValidationError as error:
            return _service_error(error)
        except WorkforceProviderError as error:
            return Response({"detail": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(ProviderOnboardingSerializer(onboarding).data)

    @action(detail=True, methods=["get"])
    def readiness(self, request, pk=None):
        readiness = payment_readiness(self.get_object())
        return Response({"ready": readiness.ready, "blockers": readiness.blockers})


class RateScheduleViewSet(viewsets.ModelViewSet):
    serializer_class = RateScheduleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if is_global_admin(self.request.user):
            return RateSchedule.objects.select_related("engagement__payer", "engagement__worker__user", "center").order_by("id")
        payer_ids = self.request.user.workforce_roles.filter(
            role=WorkforceRoleMembership.Role.WORKFORCE_ADMIN, is_active=True
        ).values_list("payer_id", flat=True)
        return RateSchedule.objects.filter(engagement__payer_id__in=payer_ids).select_related(
            "engagement__payer", "engagement__worker__user", "center"
        ).order_by("id")

    def perform_create(self, serializer):
        engagement = serializer.validated_data["engagement"]
        if not has_workforce_role(self.request.user, engagement.payer, WorkforceRoleMembership.Role.WORKFORCE_ADMIN):
            raise PermissionDenied("ClearCode workforce administration access is required.")
        serializer.save()

    def perform_update(self, serializer):
        if serializer.instance.status != RateSchedule.Status.DRAFT:
            raise PermissionDenied("Approved or retired rates are immutable.")
        if not has_workforce_role(
            self.request.user, serializer.instance.engagement.payer, WorkforceRoleMembership.Role.WORKFORCE_ADMIN
        ):
            raise PermissionDenied("ClearCode workforce administration access is required.")
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "Rates must be retired, not deleted."}, status=405)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        try:
            rate = approve_rate(rate=self.get_object(), actor=request.user)
        except DjangoPermissionDenied as error:
            raise PermissionDenied(str(error)) from error
        except ValidationError as error:
            return _service_error(error)
        return Response(self.get_serializer(rate).data)


class PayableItemViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PayableItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = PayableItem.objects.select_related(
            "engagement__payer", "engagement__worker__user", "center", "rate", "source_session"
        ).order_by("id")
        if is_global_admin(self.request.user):
            return queryset
        central_payers = self.request.user.workforce_roles.filter(is_active=True).values_list("payer_id", flat=True)
        return queryset.filter(
            Q(engagement__payer_id__in=central_payers)
            | Q(engagement__worker__user=self.request.user)
            | Q(center_id__in=managed_center_ids(self.request.user))
        ).distinct()

    @action(detail=False, methods=["post"], url_path="from-session")
    def from_session(self, request):
        input_serializer = SessionPayableInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            session = Session.objects.get(pk=input_serializer.validated_data["session"])
            payable = create_payable_from_session(session=session, actor=request.user)
        except Session.DoesNotExist:
            return Response({"detail": "Session not found."}, status=status.HTTP_404_NOT_FOUND)
        except DjangoPermissionDenied as error:
            raise PermissionDenied(str(error)) from error
        except ValidationError as error:
            return _service_error(error)
        return Response(self.get_serializer(payable).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        try:
            payable = approve_payable(payable=self.get_object(), actor=request.user)
        except DjangoPermissionDenied as error:
            raise PermissionDenied(str(error)) from error
        except ValidationError as error:
            return _service_error(error)
        return Response(self.get_serializer(payable).data)


class PaymentRunViewSet(viewsets.ModelViewSet):
    serializer_class = PaymentRunSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = PaymentRun.objects.select_related("payer").prefetch_related("payments").order_by("id")
        if is_global_admin(self.request.user):
            return queryset
        payer_ids = self.request.user.workforce_roles.filter(
            role__in=[
                WorkforceRoleMembership.Role.FINANCE_PREPARER,
                WorkforceRoleMembership.Role.FINANCE_APPROVER,
            ],
            is_active=True,
        ).values_list("payer_id", flat=True)
        return queryset.filter(payer_id__in=payer_ids)

    def perform_create(self, serializer):
        payer = serializer.validated_data["payer"]
        if not has_workforce_role(self.request.user, payer, WorkforceRoleMembership.Role.FINANCE_PREPARER):
            raise PermissionDenied("Finance preparer access is required.")
        serializer.save()

    def perform_update(self, serializer):
        if serializer.instance.status != PaymentRun.Status.DRAFT:
            raise PermissionDenied("Only a draft payment run can be edited.")
        if not has_workforce_role(
            self.request.user, serializer.instance.payer, WorkforceRoleMembership.Role.FINANCE_PREPARER
        ):
            raise PermissionDenied("Finance preparer access is required.")
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "Payment runs are retained for audit and cannot be deleted."}, status=405)

    @action(detail=True, methods=["post"], url_path="add-payables")
    def add_payables(self, request, pk=None):
        input_serializer = PaymentRunPayablesSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        try:
            payment_run = add_payables_to_run(
                payment_run=self.get_object(),
                payables=input_serializer.validated_data["payables"],
                actor=request.user,
            )
        except DjangoPermissionDenied as error:
            raise PermissionDenied(str(error)) from error
        except ValidationError as error:
            return _service_error(error)
        return Response(self.get_serializer(payment_run).data)

    def _transition(self, service):
        try:
            payment_run = service(payment_run=self.get_object(), actor=self.request.user)
        except DjangoPermissionDenied as error:
            raise PermissionDenied(str(error)) from error
        except ValidationError as error:
            return _service_error(error)
        except WorkforceProviderError as error:
            return Response({"detail": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(self.get_serializer(payment_run).data)

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        return self._transition(review_payment_run)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._transition(approve_payment_run)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        return self._transition(submit_payment_run)


@extend_schema(request=OpenApiTypes.BINARY, responses=ProviderWebhookResponseSerializer)
@api_view(["POST"])
@permission_classes([AllowAny])
def provider_webhook(request, provider: str):
    signature = request.headers.get("X-Workforce-Signature", "")
    try:
        event, created = record_provider_event(
            provider=provider,
            body=request.body,
            signature=signature,
        )
    except InvalidProviderWebhook:
        return Response({"detail": "Invalid webhook."}, status=status.HTTP_401_UNAUTHORIZED)
    except WorkforceProviderError as error:
        return Response({"detail": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response({"accepted": True, "duplicate": not created, "event_id": event.external_event_id})

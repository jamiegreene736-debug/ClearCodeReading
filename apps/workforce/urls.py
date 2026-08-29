from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.workforce.views import (
    EngagementViewSet,
    PayableItemViewSet,
    PaymentRunViewSet,
    RateScheduleViewSet,
    WorkerProfileViewSet,
    provider_webhook,
)


router = DefaultRouter()
router.register("workforce/workers", WorkerProfileViewSet, basename="workforce-worker")
router.register("workforce/engagements", EngagementViewSet, basename="workforce-engagement")
router.register("workforce/rates", RateScheduleViewSet, basename="workforce-rate")
router.register("workforce/payables", PayableItemViewSet, basename="workforce-payable")
router.register("workforce/payment-runs", PaymentRunViewSet, basename="workforce-payment-run")

urlpatterns = [
    path("workforce/provider-webhooks/<str:provider>/", provider_webhook, name="workforce-provider-webhook"),
    *router.urls,
]

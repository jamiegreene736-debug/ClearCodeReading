from django.urls import path
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response


class HealthCheckSerializer(serializers.Serializer):
    status = serializers.CharField(read_only=True)


class HealthCheckView(GenericAPIView):
    authentication_classes = []
    permission_classes = []
    serializer_class = HealthCheckSerializer

    def get(self, request):
        return Response({"status": "ok"})


urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health"),
]

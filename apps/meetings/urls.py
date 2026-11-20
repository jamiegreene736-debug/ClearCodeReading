from django.urls import path

from apps.meetings import views

urlpatterns = [
    path("", views.schedule, name="family_meetings"),
    path("new/", views.meeting_form, name="new_family_meeting"),
    path("<int:pk>/edit/", views.meeting_form, name="edit_family_meeting"),
    path("<int:pk>/cancel/", views.cancel_meeting, name="cancel_family_meeting"),
]

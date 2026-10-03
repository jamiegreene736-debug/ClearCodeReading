from django.urls import path

from apps.social import planner_views, schedule_views, views

app_name = "social"

urlpatterns = [
    path("", views.queue, name="queue"),
    path("calendar/", schedule_views.schedule_calendar, name="calendar"),
    path("posts/<int:pk>/delete/", schedule_views.delete_post, name="delete"),
    path("plan/", planner_views.planner, name="planner"),
    path("plan/action/", planner_views.plan_action, name="plan_action"),
    path("new/", views.post_edit, name="new"),
    path("posts/<int:pk>/", views.post_edit, name="edit"),
    path("posts/<int:pk>/schedule/", views.schedule, name="schedule"),
    path("posts/<int:pk>/cancel/", views.cancel, name="cancel"),
    path("posts/<int:pk>/retry/", views.retry, name="retry"),
    path("posts/<int:pk>/image/", views.image, name="image"),
    path("settings/", views.settings_page, name="settings"),
    path("settings/facebook/connect/", views.facebook_connect, name="facebook_connect"),
    path("settings/facebook/callback/", views.facebook_callback, name="facebook_callback"),
    path("settings/facebook/choose/", views.choose_page, name="choose_page"),
    path("settings/instagram/connect/", views.instagram_connect, name="instagram_connect"),
    path("settings/instagram/callback/", views.instagram_callback, name="instagram_callback"),
    path("settings/<slug:network>/disconnect/", views.disconnect, name="disconnect"),
]

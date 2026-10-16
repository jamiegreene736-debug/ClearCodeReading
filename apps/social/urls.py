from django.urls import path

from apps.social import reel_views, design_views, category_views, report_views, planner_views, schedule_views, views

app_name = "social"

urlpatterns = [
    path("reels/new/", reel_views.editor, name="reel_new"),
    path("reels/<int:pk>/", reel_views.editor, name="reel_edit"),
    path("reels/<int:pk>/generate/", reel_views.generate, name="reel_generate"),
    path("reels/<int:pk>/video/", reel_views.video, name="video"),
    path("designs/", design_views.library, name="design_library"),
    path("designs/new/", design_views.editor, name="design_editor"),
    path("posts/<int:pk>/design/", design_views.editor, name="post_design"),
    path("designs/save/", design_views.save_template, name="design_save"),
    path("designs/apply/", design_views.apply_design, name="design_apply"),
    path("designs/<int:pk>/preview/", design_views.preview, name="design_preview"),
    path("designs/<int:pk>/archive/", design_views.archive_template, name="design_archive"),
    path("performance/", report_views.performance, name="performance"),
    path("posts/<int:pk>/details/", report_views.post_details, name="post_details"),
    path("categories/", category_views.categories, name="categories"),
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
    path("posts/<int:pk>/slides/<int:slide_id>/", views.slide_image, name="slide_image"),
    path("posts/<int:pk>/generate-slide/", views.carousel_generate, name="carousel_generate"),
    path("posts/<int:pk>/image/", views.image, name="image"),
    path("settings/", views.settings_page, name="settings"),
    path("settings/facebook/connect/", views.facebook_connect, name="facebook_connect"),
    path("settings/facebook/callback/", views.facebook_callback, name="facebook_callback"),
    path("settings/facebook/choose/", views.choose_page, name="choose_page"),
    path("settings/instagram/connect/", views.instagram_connect, name="instagram_connect"),
    path("settings/instagram/callback/", views.instagram_callback, name="instagram_callback"),
    path("settings/<slug:network>/disconnect/", views.disconnect, name="disconnect"),
]

from django.urls import path

from apps.blog import manage_views

app_name = "blog_manage"

urlpatterns = [
    path("", manage_views.post_list, name="list"),
    path("new/", manage_views.post_create, name="create"),
    path("images/", manage_views.image_upload, name="image_upload"),
    path("<int:pk>/", manage_views.post_edit, name="edit"),
    path("<int:pk>/preview/", manage_views.post_preview, name="preview"),
    path("<int:pk>/publish/", manage_views.post_publish, name="publish"),
    path("<int:pk>/unpublish/", manage_views.post_unpublish, name="unpublish"),
    path("<int:pk>/feature/", manage_views.post_feature, name="feature"),
    path("<int:pk>/duplicate/", manage_views.post_duplicate, name="duplicate"),
    path("<int:pk>/delete/", manage_views.post_delete, name="delete"),
]

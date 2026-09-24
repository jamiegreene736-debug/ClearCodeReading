from django.urls import path

from apps.blog.manage_views import serve_cover, serve_image
from apps.blog.views import BlogPostDetailView, BlogPostListView

app_name = "blog"

urlpatterns = [
    path("", BlogPostListView.as_view(), name="list"),
    path("images/<uuid:key>/", serve_image, name="image"),
    path("<slug:slug>/cover/", serve_cover, name="cover"),
    path("<slug:slug>/", BlogPostDetailView.as_view(), name="detail"),
]

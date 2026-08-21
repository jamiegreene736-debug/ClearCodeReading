from django.urls import path

from apps.blog.views import BlogPostDetailView, BlogPostListView

app_name = "blog"

urlpatterns = [
    path("", BlogPostListView.as_view(), name="list"),
    path("<slug:slug>/", BlogPostDetailView.as_view(), name="detail"),
]

from django.views.generic import DetailView, ListView

from apps.blog.models import BlogPost


class BlogPostListView(ListView):
    template_name = "blog/post_list.html"
    context_object_name = "posts"
    paginate_by = 9

    def get_queryset(self):
        return BlogPost.objects.published().select_related("author")


class BlogPostDetailView(DetailView):
    template_name = "blog/post_detail.html"
    context_object_name = "post"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return BlogPost.objects.published().select_related("author")

from django.views.generic import DetailView, ListView

from apps.blog.models import BlogPost
from apps.blog.substack import (
    SUBSTACK_PROFILE_URL,
    SUBSTACK_SUBSCRIBE_URL,
    get_substack_posts,
)


class BlogPostListView(ListView):
    template_name = "blog/post_list.html"
    context_object_name = "posts"
    paginate_by = 9

    def get_queryset(self):
        local_posts = list(BlogPost.objects.published().select_related("author"))
        posts = [*local_posts, *get_substack_posts()]
        return sorted(
            posts,
            key=lambda post: (post.is_featured, post.published_at),
            reverse=True,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "substack_profile_url": SUBSTACK_PROFILE_URL,
                "substack_subscribe_url": SUBSTACK_SUBSCRIBE_URL,
            }
        )
        return context


class BlogPostDetailView(DetailView):
    template_name = "blog/post_detail.html"
    context_object_name = "post"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return BlogPost.objects.published().select_related("author")

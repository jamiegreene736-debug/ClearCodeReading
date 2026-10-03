from typing import Any

from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from apps.blog.models import BlogPost
from apps.blog.promotion import sync_promotion
from apps.social.models import SocialPost


@receiver(post_save, sender=BlogPost)
def article_saved(
    sender: type[BlogPost], instance: BlogPost, raw: bool = False, **kwargs: Any
) -> None:
    if not raw:
        sync_promotion(instance)


@receiver(pre_delete, sender=BlogPost)
def article_removed(sender: type[BlogPost], instance: BlogPost, **kwargs: Any) -> None:
    SocialPost.objects.filter(
        blog_post=instance, status=SocialPost.Status.SCHEDULED
    ).update(
        status=SocialPost.Status.ATTENTION,
        last_error="The linked article was removed. This feature cannot be sent.",
    )

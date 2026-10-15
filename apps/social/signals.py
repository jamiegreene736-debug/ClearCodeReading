"""Default only newly created blog promotions to the internal blog category."""

from django.db.models.signals import pre_save
from django.dispatch import receiver

from apps.social.models import SocialCategory, SocialPost


@receiver(pre_save, sender=SocialPost)
def assign_blog_category(
    sender: type[SocialPost], instance: SocialPost, raw: bool, **kwargs: object
) -> None:
    if (
        not raw
        and instance._state.adding
        and not instance.category_id
        and instance.is_blog_promotion
    ):
        instance.category = SocialCategory.objects.filter(
            system_key="blog", archived=False
        ).first()

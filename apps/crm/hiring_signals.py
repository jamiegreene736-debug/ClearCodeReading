from typing import Any

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.models import RecruitingInterest
from apps.crm.hiring import initialize_candidate


@receiver(post_save, sender=RecruitingInterest)
def enroll_teacher_application(
    sender: type[RecruitingInterest],
    instance: RecruitingInterest,
    created: bool,
    raw: bool = False,
    **kwargs: Any,
) -> None:
    if (
        not raw
        and created
        and instance.career_path == RecruitingInterest.CareerPath.TEACHER
    ):
        initialize_candidate(instance)

"""Editable image direction, validated before any generation or saved-plan reset."""

from __future__ import annotations

from apps.social.ai import _guard
from apps.social.exceptions import SocialError
from apps.social.models import SocialPost


def image_prompt_text(value: str) -> str:
    value = value.strip()
    if value and not 12 <= len(value) <= 4000:
        raise SocialError(
            "Use 12–4,000 characters for the image prompt, or leave it blank to use the captions."
        )
    _guard(value)
    return value


def carousel_prompts(values: list[str], count: str) -> list[str] | None:
    if not any(value.strip() for value in values):
        return None
    if count not in {str(number) for number in range(2, 11)} or len(values) != int(
        count
    ):
        raise SocialError("Choose 2–10 slides and enter one prompt for each slide.")
    prompts = [value.strip() for value in values]
    for prompt in prompts:
        if not 12 <= len(prompt) <= 2000:
            raise SocialError(
                "Use 12–2,000 characters for every carousel slide prompt."
            )
        _guard(prompt)
    return prompts


def update_prompts(post: SocialPost, prompts: list[str]) -> bool:
    if prompts == post.carousel_plan.get("slides"):
        return False
    if post.status != SocialPost.Status.DRAFT or (
        post.pk and post.publications.exists()
    ):
        raise SocialError(
            "Cancel the schedule before changing carousel prompts. Only unpublished drafts can generate images."
        )
    post.carousel_plan = {"slides": prompts, "completed": 0}
    return True

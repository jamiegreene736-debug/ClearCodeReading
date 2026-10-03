"""Named return destinations keep navigation local and independent of browser history."""

from django.http import HttpRequest
from django.urls import reverse

from apps.social.models import SocialPost

LIST_LABELS = {
    "drafts": "Drafts",
    "scheduled": "Scheduled",
    "posted": "Posted",
    "attention": "Needs attention",
}


def navigation(request: HttpRequest, post: SocialPost | None = None) -> dict[str, str]:
    default = "drafts"
    if post is not None:
        status_tabs: dict[str, str] = {
            SocialPost.Status.SCHEDULED: "scheduled",
            SocialPost.Status.POSTED: "posted",
            SocialPost.Status.PUBLISHING: "scheduled",
            SocialPost.Status.ATTENTION: "attention",
        }
        default = status_tabs.get(post.status, "drafts")
    route = request.resolver_match.url_name if request.resolver_match else ""
    origin = request.GET.get("return_to", default)
    if route == "queue":
        default = "scheduled"
        origin = request.GET.get("tab", "scheduled")
    if origin not in {*LIST_LABELS, "planner"}:
        origin = default
    if route == "planner" and origin == "planner":
        origin = "drafts"
    if origin == "planner":
        url, label = reverse("social:planner"), "AI content plan"
    else:
        url, label = f"{reverse('social:queue')}?tab={origin}", LIST_LABELS[origin]
    back_url, back_label = url, label
    if route == "choose_page":
        back_url, back_label = reverse("social:settings"), "Settings"
    elif route == "schedule" and request.GET.get("via") == "edit" and post is not None:
        back_url = (
            f"{reverse('social:edit', kwargs={'pk': post.pk})}?return_to={origin}"
        )
        back_label = "post"
    return {
        "origin": origin,
        "url": url,
        "label": label,
        "back_url": back_url,
        "back_label": back_label,
        "via": "edit" if request.GET.get("via") == "edit" else "",
        "link_origin": "planner" if route == "planner" else origin,
        "active": origin if route == "queue" else route or "",
    }

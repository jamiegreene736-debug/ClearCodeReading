import time
from pathlib import Path
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import slugify

from apps.resources.forms import REVISION_FIELDS, ResourceForm, validate_publication
from apps.resources.models import Asset, Resource, ResourceEvent, Revision, UploadBatch
from apps.resources.uploads import MAX_BATCH_SIZE, save_upload


class EditConflict(ValidationError):
    pass


def revision_values(revision: Revision) -> dict:
    return {field: getattr(revision, field) for field in REVISION_FIELDS}


def check_version(resource: Resource, version: str | int | None) -> None:
    if str(resource.version) != str(version):
        raise EditConflict(
            "This resource changed in another tab. Reload before saving; your edits have not overwritten it."
        )


def record_change(resource: Resource, user, action: str) -> None:
    resource.version += 1
    resource.save()
    ResourceEvent.objects.create(
        resource=resource, actor=user, action=action, revision=resource.draft
    )


def create_resource(
    user, *, kind: str, title: str = "Untitled resource", **values
) -> Resource:
    resource = Resource.objects.create(
        owner=user, slug=f"{slugify(title)[:120] or 'resource'}-{uuid4().hex[:8]}"
    )
    resource.draft = Revision.objects.create(
        resource=resource, author=user, kind=kind, title=title, **values
    )
    record_change(resource, user, "created")
    return resource


def save_draft(resource: Resource, form: ResourceForm, user) -> Revision:
    check_version(resource, form.cleaned_data["version"])
    draft = form.save(commit=False)
    if form.cleaned_data.get("upload"):
        draft.asset = save_upload(form.cleaned_data["upload"], user)
        if draft.title == "Untitled resource":
            draft.title = (
                Path(draft.asset.name).stem.replace("_", " ").replace("-", " ")[:200]
            )
    if form.cleaned_data.get("remove_cover"):
        draft.cover = None
        draft.cover_alt = ""
    if form.cleaned_data.get("cover_upload"):
        draft.cover = save_upload(
            form.cleaned_data["cover_upload"], user, image_only=True
        )
    if draft.kind != Revision.Kind.FILE:
        draft.asset = None
    if draft.kind != Revision.Kind.LINK:
        draft.url = ""
    if draft.kind != Revision.Kind.ARTICLE:
        draft.body = ""
    if resource.draft_id and revision_values(resource.draft) == revision_values(draft):
        return resource.draft
    draft.resource = resource
    draft.author = user
    draft.save()
    resource.draft = draft
    resource.submitted = False
    record_change(resource, user, "draft_saved")
    return draft


def publish(resource: Resource, user, *, at=None) -> None:
    if resource.archived:
        raise ValidationError("Restore this resource before publishing.")
    validate_publication(resource.draft)
    if at:
        # Preserve the already-visible scheduled version when rescheduling after it went live.
        current = resource.published_revision
        resource.live = current
        resource.scheduled = resource.draft
        resource.publish_at = at
    else:
        resource.live = resource.draft
        resource.scheduled = None
        resource.publish_at = None
    resource.submitted = False
    record_change(resource, user, "scheduled" if at else "published")


def duplicate(resource: Resource, user) -> Resource:
    values = revision_values(resource.draft)
    values["title"] = f"{values['title'][:190]} (copy)"
    values["featured"] = False
    # A reviewer can reuse another author's files; copies remain owned by that author
    # only for the original. Copy file metadata/data for the new author's private picker.
    for field in ("asset", "cover"):
        asset = values[field]
        if asset and asset.owner_id != user.pk:
            values[field] = Asset.objects.create(
                owner=user,
                name=asset.name,
                content_type=asset.content_type,
                size=asset.size,
                digest=asset.digest,
                data=asset.data,
                preview=asset.preview,
            )
    return create_resource(user, **values)


@transaction.atomic
def bulk_upload(user, uploads: list, token, topic, audience: str) -> list[Resource]:
    if not uploads or len(uploads) > 10:
        raise ValidationError("Choose between 1 and 10 files.")
    if sum(upload.size for upload in uploads) > MAX_BATCH_SIZE:
        raise ValidationError("The combined upload must be 50 MB or smaller.")
    batch, created = UploadBatch.objects.get_or_create(
        token=token, defaults={"owner": user}
    )
    if not created:
        if batch.owner_id != user.pk:
            raise ValidationError("Start a new upload from the Resource Manager.")
        return list(batch.resources.all())
    resources = []
    deadline = time.monotonic() + 20
    for upload in uploads:
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                raise ValidationError(
                    "This batch took too long to prepare. Try fewer files at a time."
                )
            asset = save_upload(upload, user, render_timeout=min(8, remaining))
        except ValidationError as exc:
            raise ValidationError(
                f"{upload.name}: {' '.join(exc.messages)} No files were added."
            ) from exc
        resource = create_resource(
            user,
            kind=Revision.Kind.FILE,
            title=Path(asset.name).stem.replace("_", " ").replace("-", " ")[:200],
            asset=asset,
            topic=topic,
            audience=audience,
        )
        resources.append(resource)
    batch.resources.set(resources)
    return resources

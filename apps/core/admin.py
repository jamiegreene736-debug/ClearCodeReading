from io import BytesIO

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html

from apps.core.models import RecruitingInterest


admin.site.site_header = "ClearCode Reading Admin Portal"
admin.site.site_title = "ClearCode Reading Admin Portal"
admin.site.index_title = "Admin Portal"
admin.site.enable_nav_sidebar = False
admin.site.index_template = "admin/clearcode_index.html"
admin.site.app_index_template = "admin/clearcode_app_index.html"


@admin.register(RecruitingInterest)
class RecruitingInterestAdmin(admin.ModelAdmin):
    document_fields = {
        "resume": ("resume_data", "resume_original_name", "resume_content_type", "resume"),
        "cover-letter": (
            "cover_letter_data",
            "cover_letter_original_name",
            "cover_letter_content_type",
            "cover_letter",
        ),
    }
    list_display = (
        "name",
        "email",
        "career_path",
        "how_heard",
        "candidate_pool",
        "owner",
        "status",
        "created_at",
    )
    list_filter = ("career_path", "candidate_pool", "owner", "status", "created_at")
    search_fields = ("name", "email", "phone", "address", "how_heard", "role_interest", "notes")
    readonly_fields = (
        "resume_download",
        "cover_letter_download",
        "created_at",
        "updated_at",
        "source_path",
    )
    fieldsets = (
        (
            "Contact",
            {
                "fields": (
                    "name",
                    "email",
                    "phone",
                    "address",
                    "how_heard",
                    "career_path",
                    "role_interest",
                    "notes",
                )
            },
        ),
        ("Documents", {"fields": ("resume_download", "cover_letter_download")}),
        (
            "Workflow",
            {
                "fields": (
                    "candidate_pool",
                    "owner",
                    "status",
                    "source_path",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )
    autocomplete_fields = ("owner",)

    def get_urls(self):
        return [
            path(
                "<path:object_id>/documents/<str:document_kind>/",
                self.admin_site.admin_view(self.document_download),
                name="core_recruitinginterest_document",
            ),
        ] + super().get_urls()

    @admin.display(description="Resume")
    def resume_download(self, obj):
        return self._document_link(obj, "resume", "Download resume")

    @admin.display(description="Cover letter")
    def cover_letter_download(self, obj):
        return self._document_link(obj, "cover-letter", "Download cover letter")

    @staticmethod
    def _document_link(obj, document_kind, label):
        data_field, _original_name_field, _content_type_field, legacy_field = (
            RecruitingInterestAdmin.document_fields[document_kind]
        )
        if not obj or not (getattr(obj, data_field) or getattr(obj, legacy_field)):
            return "—"
        return format_html(
            '<a href="{}">{}</a>',
            reverse(
                "admin:core_recruitinginterest_document",
                args=(obj.pk, document_kind),
            ),
            label,
        )

    def document_download(self, request, object_id, document_kind):
        document_config = self.document_fields.get(document_kind)
        if document_config is None:
            raise Http404("Document not found.")

        interest = get_object_or_404(RecruitingInterest, pk=object_id)
        if not self.has_view_permission(request, interest):
            raise PermissionDenied

        data_field, original_name_field, content_type_field, legacy_field = document_config
        document_data = getattr(interest, data_field)
        legacy_document = getattr(interest, legacy_field)
        if not document_data and not legacy_document:
            raise Http404("Document not found.")

        if document_data:
            document = BytesIO(bytes(document_data))
            content_type = getattr(interest, content_type_field) or "application/octet-stream"
        else:
            try:
                legacy_document.open("rb")
            except FileNotFoundError as exc:
                raise Http404("Document not found.") from exc
            document = legacy_document
            content_type = None

        response = FileResponse(
            document,
            as_attachment=True,
            filename=getattr(interest, original_name_field) or legacy_document.name,
            content_type=content_type,
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

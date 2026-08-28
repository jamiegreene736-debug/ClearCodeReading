from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html

from apps.core.models import RecruitingInterest


@admin.register(RecruitingInterest)
class RecruitingInterestAdmin(admin.ModelAdmin):
    document_fields = {
        "resume": ("resume", "resume_original_name"),
        "cover-letter": ("cover_letter", "cover_letter_original_name"),
    }
    list_display = ("name", "email", "career_path", "how_heard", "status", "created_at")
    list_filter = ("career_path", "status", "created_at")
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
        ("Workflow", {"fields": ("status", "source_path", "created_at", "updated_at")}),
    )

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
        field_name, _original_name_field = RecruitingInterestAdmin.document_fields[document_kind]
        if not obj or not getattr(obj, field_name):
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

        field_name, original_name_field = document_config
        document = getattr(interest, field_name)
        if not document:
            raise Http404("Document not found.")
        try:
            document.open("rb")
        except FileNotFoundError as exc:
            raise Http404("Document not found.") from exc

        response = FileResponse(
            document,
            as_attachment=True,
            filename=getattr(interest, original_name_field) or document.name,
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

from django.urls import path

from apps.crm_email import views
from apps.crm_email.stage_views import first_stage

urlpatterns = [
    path("email/first-stage/", first_stage, name="crm_first_stage_emails"),
    path("email/", views.settings_view, name="crm_email_settings"),
    path("email/connect/", views.connect_view, name="crm_email_connect"),
    path("email/callback/", views.callback, name="crm_email_callback"),
    path("email/push/", views.push, name="crm_email_push"),
    path("email/sync/", views.sync_view, name="crm_email_sync"),
    path(
        "email/mailboxes/<int:mailbox_id>/disconnect/",
        views.disconnect_view,
        name="crm_email_disconnect",
    ),
    path(
        "email/automated/images/upload/",
        views.automated_image_upload,
        name="crm_email_automated_image_upload",
    ),
    path(
        "email/images/<uuid:image_id>/",
        views.automated_image,
        name="crm_email_automated_image",
    ),
    path(
        "email/automated/<slug:key>/",
        views.automated_email_view,
        name="crm_email_automated",
    ),
    path("email/newsletters/new/", views.newsletter_edit, name="crm_newsletter_new"),
    path(
        "email/newsletters/<int:campaign_id>/",
        views.newsletter_edit,
        name="crm_newsletter",
    ),
    path(
        "email/newsletters/<int:campaign_id>/send/",
        views.newsletter_send,
        name="crm_newsletter_send",
    ),
    path("email/templates/new/", views.template_view, name="crm_email_template_new"),
    path(
        "email/templates/<int:template_id>/",
        views.template_view,
        name="crm_email_template",
    ),
    path(
        "email/attachments/<int:attachment_id>/",
        views.download,
        name="crm_email_download",
    ),
    path(
        "email/attachments/<int:attachment_id>/remove/",
        views.remove_attachment,
        name="crm_email_attachment_remove",
    ),
    path("contacts/<int:pk>/email/", views.contact_email, name="crm_contact_email"),
    path("contacts/<int:pk>/email/compose/", views.compose, name="crm_email_compose"),
    path("contacts/<int:pk>/email/import/", views.import_view, name="crm_email_import"),
    path(
        "contacts/<int:pk>/email/threads/<int:conversation_id>/",
        views.conversation_view,
        name="crm_email_thread",
    ),
    path(
        "contacts/<int:pk>/email/messages/<uuid:message_id>/cancel/",
        views.cancel_message,
        name="crm_email_cancel",
    ),
]

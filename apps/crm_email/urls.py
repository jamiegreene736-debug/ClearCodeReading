from django.urls import path

from apps.crm_email import views

urlpatterns = [
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

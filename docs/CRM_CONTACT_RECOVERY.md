# Recover a deleted CRM contact

Administrators (superusers or the Super Admin role) can delete a contact from its detail page after confirming the modal. Contact API and Django admin deletions also retain the database row. Ordinary CRM team members cannot delete contacts.

Deletion sets `Lead.is_deleted` and `deleted_at`, and writes `crm.contact.deleted` to `AuditLog` with the administrator and timestamp. Contact lists, detail pages, API lists, and contact task/triage queues exclude deleted contacts. Linked deals, form submissions, notes, tasks, and user accounts remain intact. Deletion does not unsubscribe a separate newsletter subscription or cancel independently scheduled communications.

To restore on a future authorized request, identify the exact deleted contact ID and an active administrator ID in the correct database. Inspect `Lead.objects.filter(is_deleted=True)` and the corresponding audit log before choosing the record. Run:

```sh
python manage.py restore_crm_contact CONTACT_ID --actor-id ADMIN_USER_ID
```

The command clears the deletion flags and records `crm.contact.restored`. It is idempotent and preserves the original ID and relationships. Verify the contact detail page and its history after restoration. There is no automatic purge or expiration introduced by this feature.

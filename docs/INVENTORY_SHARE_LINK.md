# Shareable Parent Reading Inventory

Staff open CRM → Inventories → Create inventory link. The generated URL is reusable across families; subsequent clicks return the same link. Creating or viewing the link requires CRM access. No email is sent by creating the link or starting intake.

The public intake requires the parent's first name, last name and email, plus the child's name, age in years (3–21) and current grade. Grade selects the existing question set. A contact is created at intake so incomplete inventories can be followed up. An active existing contact is matched by case-insensitive email without changing staff-entered fields, ownership, priority, or status. Names submitted for existing contacts are recorded in the inventory activity. Age is stored on the child and shown on the staff inventory detail page.

Every new intake gets a separate private invitation; matching an email never reveals previous answers. A signed, expiring form identity makes repeated posts idempotent. The reusable link's database lock serializes these intakes and contact matching. Intake has CSRF protection, a honeypot, and the existing cache-backed public rate-limit pattern (20 attempts per IP/hour). Production should use a shared cache for a cross-worker rate limit.

The existing inventory handles answers, 30-day private-link expiry, scoring, review tasks, and completion follow-up emails. Completed inventories appear in Needs review on the matched contact. The public intake does not subscribe the parent to newsletters.

Migration 0031 adds a nullable age field for existing children and the reusable-link table. Existing invitation links continue working.

Verification: `python manage.py test apps.crm.test_inventory_intake apps.crm.test_inventory apps.crm.test_contact_lifecycle apps.crm.test_consultation_booking --noinput`, Django system/migration checks, Ruff on the new modules, and GitHub's full test suite. New tests cover required fields, staff access, contact preservation, duplicate posts, spam/CSRF/tampered links, atomic rollback, and completion routing.

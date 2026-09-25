# Teacher hiring workspace

## Workflow

Open **CRM → Teacher hiring**. The sidebar count is teachers still in
Application received (pending intake) and opens that queue. The page leads with
team queues: pending intake, needs attention, needs owner, interviews, offers
waiting on a response, on hold, and ready for assignment. Stage counts under
those queues follow the owner filter. My candidates is still the default list.
Use Everyone, an individual owner, Needs owner, stage, name/email, or Needs
attention to find work. Needs attention includes overdue dates, missing dates,
unavailable owners, and recorded blockers. Results are paginated and attention
items sort first. The overview shows the same pending-intake count for people
with hiring access.

One person owns the entire application. Only the current owner edits evaluation,
decision, offer, and onboarding records. Any authorized hiring team member can
transfer full ownership; that action is recorded. There is no second approval.

The stages are Application received → Initial screening → Interview / teaching
demonstration → Hiring decision → Offer sent → Onboarding → Ready for assignment.
Earlier checklists must be complete to advance. The decision stage requires an
evaluation. Offers require the owner's decision, rationale, sent date, and terms.
Onboarding requires a dated acceptance. Ready requires paperwork, training, and
the owner's readiness confirmation. This records recruiting readiness; it does
not create a teacher login or bypass workforce scheduling/payment eligibility.

Changing stages supplies a suggested next action and a two-business-day due date
when the owner has not changed those values. On hold requires a reason and review
date, which becomes its due date. Not selected and Withdrawn require a reason.
Closed/ready records remain searchable under All stages or their specific stage.
Reopening a candidate preserves the earlier records and validates the new stage.

Offer fields record communications already sent. Saving never sends an email,
text, offer, or agreement. Tax/bank information belongs in the workforce system.

## Intake and existing records

The canonical application and owner remain `core.RecruitingInterest`; the CRM
workflow is a one-to-one `HiringCandidate`. New teacher applications enroll
immediately in the same transaction as public intake. Company applications and
teacher product inquiries are not automatically enrolled.

The data migration adds existing teacher applications and preserves owners,
original application timestamps, documents, and notes. Historical Closed records
become On hold with an explicit outcome-review action, since Closed did not say
whether someone was hired, declined, or withdrew. No prior hiring success is
inferred. Re-running the import does not duplicate candidates or initial events.

For a CRM contact who confirms hiring interest, use **Teacher hiring → Confirm
hiring interest and open candidate** on the contact record. An existing
application with the same email is opened rather than duplicated. The original
CRM contact, relationships, and sales status remain unchanged.

New intake prefers the eligible `RECRUITING_OWNER_EMAIL`, then balances open
candidates across explicitly enabled hiring users; otherwise an eligible
administrator owns intake. If none exists, the Needs owner queue exposes it.

## Access and data handling

Administrators retain hiring access. Other CRM users require `hiring_enabled`,
managed by a super administrator in **CRM → Team**. Enable Bethany and Brook's
existing CRM accounts there if they do not already have access. Existing active
CRM recruiting owners retain access during migration. A hiring flag does not
grant CRM access to teachers, guardians, students, or school administrators.

Resume and cover-letter downloads require hiring authorization, force attachment
downloads, and use private/no-store and nosniff headers. Candidate lists defer
binary document fields. Evaluation notes stay out of generic CRM contact activity
and public APIs. The legacy recruiting admin links to the hiring workspace and
makes teacher ownership/status read-only so workflow edits are audited here.

Updates lock the candidate and application, compare the submitted revision, and
save the record and history atomically. Stale saves and stale reassignments fail
with a reload instruction. All mutations require POST and CSRF protection.

## Verification

Run the Django suite against PostgreSQL, especially `apps.crm.test_hiring`,
`apps.crm`, `apps.users`, and `apps.core`. Run `manage.py check`,
`manage.py makemigrations --check --dry-run`, and `git diff --check`.
Browser QA covers My candidates/Everyone, candidate selection, non-owner read-only
state, save/reload, and widths 1440, 1024, 390, and 320 pixels.

# OG+ family prototype and weekly assessment

The family prototype uses the publisher's concept numbering, not the identifiers
in the separate ClearCode instructional graph. The selected reader's plan begins
at 26 (qu), skips 27 as explicitly assigned, and continues in order starting at
28 (ch), through 83 unless the instructional team changes the plan.

A program operator can provision the verified reader and the three first-concept
Drive file links with `manage.py provision_og_family_plan --schema <school-schema>
--child-id <verified-reader-id> --first 26 --last 83 --skip 27 --letter <url>
--fluency <url> --passage <url>`. This command requires active guardian consent,
is idempotent, and refuses to replace a different existing plan or resource pack.
To add future resources, repeat the same plan settings with `--concept <number>` and that concept’s three file URLs.
Do not place student identities or resource URLs in source control. Drive links
retain their existing Google sharing permissions; verify the family can access
the files separately and never make a private folder public for this workflow.

The assigned teacher opens **Record OG+ weekly assessment** from the reader's
dashboard. The entry covers word and sentence dictation, oral reading fluency,
and decodable passage comprehension. Enter correct/attempted fractions and
(optional) total words attempted to have the portal calculate reading accuracy.
The teacher must explicitly mark the current concept passed to unlock the
matching Family Connection Letter, Fluency Practice, and Decodable Passage.
Saving scores alone never unlocks resources or changes the concept. The
guardian dashboard and progress API omit resource links before the pass and
exclude private teacher notes. The workflow sends no automatic emails.

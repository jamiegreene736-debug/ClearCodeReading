# Social categories and analytics

Posts have one optional internal category. Categories have a shared, validated hex color,
case-insensitively unique name, and an archive flag. The seeded categories are Free resources,
Blog posts, Reading tips, Programs & services, and Community & updates. Uncategorized is a
null category, not a separate editable record. Existing and newly created blog promotions
receive Blog posts; other older posts remain uncategorized. Explicit later assignments are
preserved. Categories are independent of publication source, format, and status.

The composer can create categories inline. Changing a shared color requires an explicit
checkbox. The Categories page supports rename, recolor, archive/restore and paginated bulk
assignment. Category & results allows metadata-only edits after publication. These operations
use the existing super-administrator authorization and CSRF checks. No republishing occurs.
Calendar colors identify category, while status is text. Filters persist between months,
and monthly category counts exclude the adjacent-month days. Direct publications without a
scheduled date appear using their publication timestamp.

## Analytics operation

Run migrations before deploying the web app and analytics worker. Celery Beat schedules
`apps.social.tasks.sync_social_insights` hourly, in the public schema. Installations without
Celery Beat can run `python manage.py sync_social_insights` hourly as a separate scheduled
job. Do not put analytics into the publishing loop. The collector processes at most ten due
publications per run. Recent publications refresh daily; those older than 30 days refresh
weekly. Increase run frequency for larger backlogs. Per-publication leases prevent concurrent
requests; account-level cooldowns prevent repeated permission or outage failures from starving
the other platform. Retries use bounded exponential backoff with jitter. All requests are reads.

Standard publishing sign-in is unchanged. Connections offers separate Enable analytics access
buttons, requesting `read_insights` and `instagram_manage_insights` through Facebook Login, or
`instagram_business_manage_insights` through Instagram Login. Meta app approval and an eligible
account/token may be required. Reconnecting resets the analytics cooldown and queues another
collection. This implementation does not establish that an existing production token has
these permissions, nor does a passing mocked test establish live metric availability.

Provider requests use the app's configured Graph API version. Facebook requests reactions,
comments, shares, post_media_view, post_total_media_view_unique and link clicks from
post_clicks_by_type (not all clicks). Instagram requests like_count, comments_count, views,
reach, shares and saved. Each metric is isolated so a metric unsupported by the post type or
API version does not discard other available data. Permission/rate-limit failures stop further
requests immediately, retain available basic counts, and display a sanitized explanation.
Empty/malformed counts are unavailable, never zero; actual zero is preserved. No tokens or
raw provider payloads appear in reports or logs. No commenter identities are collected.

Each publication has last-known metrics, per-metric observation timestamps, missing-data
reasons and sync state. Successful data produces one updateable daily UTC snapshot. A failed
refresh preserves prior values and history. These are observed cumulative values, not daily
increments. The first-week comparison chooses the earliest real snapshot captured 7–8 days
after publication; older posts without such a snapshot are unavailable, not reconstructed.

The Performance page filters by publication date (up to 366 days), category and platform.
It distinguishes content count from platform-publication count. Category totals and averages
include observed values only and show coverage. Unique reach is never summed. CSV exports
share the filters and escape spreadsheet formula prefixes. Individual post results show
per-metric timestamps, missing-data notes and recent snapshots. Request refresh queues the
next worker run with a 15-minute minimum interval and preserves account cooldowns.

## Verification

`python manage.py test apps.social apps.blog apps.core.test_marketing --noinput`

`mypy --config-file mypy-social.ini`

Browser checks include existing composer, carousel and navigation flows plus categories,
calendar and performance layouts. Tests mock provider calls for zero/missing values, both
Instagram login paths, partial permission results, rate limits, malformed data, retries,
leases, retention, per-platform totals, snapshot windows, CSV safety, authorization and
metadata-only edits. No real post is published by analytics tests.

# AI blog planning and linked Facebook promotion

Build on the social planner and existing future-dated BlogPost publication. Maintain
four weekly article previews, with explicit reading topics, a complete article,
cover/alt text, SEO summary, source context, independent review, and Facebook teaser.
Use the same approved public context, brand rules and OpenAI service; no student/CRM
records. Render escaped structured sections, not AI-supplied HTML. Default Wednesday
09:00 Eastern; optional Facebook link one hour later. No Instagram link posts.

Modes: paused, review first, automatic. Initial rollout prepares drafts only. Automatic
schedules approved articles and optional Facebook promotions transactionally. Manual
articles can also be featured via a dedicated scheduling form. Promotions are actual
SocialPost rows in Marketing > Social media, labeled Blog feature, with links back to
the article. One promotion per article prevents duplicate delivery. Facebook uses the
link-sharing endpoint so its preview points to the full article/cover.

Rescheduling a blog shifts queued promotion by the same interval; unpublishing or
deleting holds it. Check the article's current state, canonical URL and anonymous HTTP
availability immediately before a Facebook send. Failed/ambiguous sends require review.
Pause removes future automatically scheduled articles and their queued promotions;
already published articles and individually scheduled work remain intact. Skipping and
manual cancellation must not be undone by a later worker pass. Existing worker runs
both planners, with separate locks, bounded retries, persisted text, and visible errors.

Verify: private/future articles, article/teaser consistency, escaped content and grounded
sources, pause/skip/retry, duplicate runs, public readiness, reschedule/delete/unpublish,
manual and automatic promotion, permissions, combined queue, migrations and full suite.
Merge, deploy both services, then inspect real generated drafts and the live schedule UI.

Implemented in `apps/blog/planner*.py` and `promotion.py`, using the existing
five-minute social worker and OpenAI configuration. Automatic articles use the blog's
existing date-gated publication; Facebook additionally checks the anonymous live page.
Changing prepared content requires manual editorial scheduling. Facebook determines
whether/how to display the supplied Open Graph cover; the linked feature itself is
always a link post. Initial automation stays paused with previews available.

Validation: 803 full-suite tests passed; the final focused blog/social/marketing suite
passed 163 tests, including four additional delivery, retry, timezone and worker-isolation
cases. Strict typing passed for eight planner/service modules; lint, formatting,
migration consistency and generated CSS checks passed.

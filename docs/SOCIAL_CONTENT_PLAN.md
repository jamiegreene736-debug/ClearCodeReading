# Weekly AI content plan

## Design and acceptance criteria

- One editable brand plan, super-administrator access only. One weekly slot; maintain
  four upcoming weeks. Modes: paused, review first, automatic. Eastern wall-clock time.
- Ground every idea in `docs/BRAND_SYSTEM.md`, the curated public facts in
  `apps/social/editorial.py`, administrator priorities, and recent social captions.
  No CRM records, student data, private messages, or unbounded web scraping are inputs.
- Rotate useful reading routines, literacy explanations, encouragement, and soft
  invitations. Use one original square illustration and separate Facebook/Instagram
  captions; no invented offers, opening dates, outcomes, or testimonials.
- OpenAI structured output supplies the concept/captions. A separate editorial check
  must pass before automatic scheduling. Preserve the source/context snapshot and
  explain each idea in the preview. This is a quality gate, not a factual guarantee.
- Generate outside web requests. The existing five-minute publisher tops up the plan.
  Database uniqueness and a PostgreSQL advisory lock prevent duplicate slots/calls.
  Persist text before image generation, use bounded retries/backoff, and show errors.
- Pause removes future plan posts from the schedule; skipped/canceled weeks stay
  skipped. Manual posts are unaffected. Settings changes affect unfinished/new slots;
  prepared posts keep their previewed content and time.
- Validate access, malformed/refused AI responses, provider failures, repetition,
  context isolation, pause/skip races, duplicate runs, DST, and scheduler integration.
- Merge the PR, verify both Railway services, generate live previews, and verify the
  selected mode. No immediate public test post is required.

## Research

- OpenAI Structured Outputs: https://developers.openai.com/api/docs/guides/structured-outputs
  Strict schema responses plus application validation; handle refusal/incomplete output.
- OpenAI GPT Image 2: https://developers.openai.com/api/docs/models/gpt-image-2
  Retain the deployed image model and generate one image reused on both networks.
- Meta original content guidance:
  https://about.fb.com/news/2026/03/rewarding-original-creators-on-facebook/
  Create original, useful material instead of republishing others' posts.
- Reading Rockets, parent phonics/decoding overview:
  https://www.readingrockets.org/literacy-home/reading-101-guide-parents/reading-basics/phonics-and-decoding
  Topic reference only; do not fabricate studies, quantitative claims, or quotes.

- OpenAI GPT-6 Luna: https://developers.openai.com/api/docs/models/gpt-6-luna
  Dedicated weekly writer/reviewer model with low reasoning effort and structured output;
  configure `SOCIAL_AI_PLANNER_MODEL` independently of the manual caption composer.
  Explicit directions keep the four editorial themes distinct; brand weeks must name
  ClearCode and describe structured literacy in both captions.

## Operating notes

One post weekly is a conservative starting cadence, not a proven optimal posting
frequency or time. Change the day/time using actual audience results. Automation
uses the account's OpenAI API billing. Four preview slots bound initial generation;
normal operation generates one replacement per week. Failed requests may still incur
provider charges. There is no automatic retry of ambiguous public publishing failures.

## Calendar and deletion

`/portal/marketing/calendar/` displays a navigable month of dated social posts,
including linked blog features, in Eastern time. Scheduled, sending, posted and
attention states remain distinguishable. Unscheduled drafts and AI preview dates
are excluded so the calendar reflects actual publishing commitments. On narrow
screens, days become a readable chronological list.

Draft and scheduled posts can be deleted from the queue, composer, AI plan or
calendar after a POST/CSRF confirmation. Deletion serializes with the publishing
worker and both planners, refuses sending/published or partially published posts,
and marks an AI social slot skipped before removing its post. Deleting a linked
Facebook feature preserves its blog article. A stale editor cannot recreate the
removed row, and the worker tolerates a deletion after its due-post snapshot.

# Story-driven carousels and AI Reels

## Scope and acceptance criteria

1. Carousel planning preserves explicit objects and emotions, and assigns distinct story beats rather than repeating teaching scenes. The tablet example must include a disengaged child and tablet, a request for human connection, and shared reading. Respectful frustration is allowed.
2. A separate Reel composer plans an editable three-beat moving story, writes Facebook/Instagram captions, and generates a 12-second 9:16 MP4 using Sora. No uploaded human reference images. Fictional characters only. User selected actual AI-generated moving video, not animated photos.
3. Generation is asynchronous and explicitly started after storyboard review. Saved job IDs survive navigation; status checks never create another paid job. Ambiguous submissions are held for manual investigation. Completed MP4s persist with the draft and can be previewed/downloaded before scheduling.
4. Existing scheduling/publishing supports Reels on Facebook and Instagram. Provider upload/container IDs persist before subsequent steps. Ambiguous final submissions are held for reconciliation rather than blindly repeated. Photo/carousel publishing remains unchanged.
5. Mocked tests cover prompts, generation, invalid responses, provider errors, permissions, content serving, and publishing checkpoints. Run Django checks, migrations check, focused tests, lint, and typing.

## Delivery and limitations

Use the existing server OpenAI key, with optional SOCIAL_AI_VIDEO_MODEL (sora-2 by default). Each explicit generation is billable; do not make paid production calls during development. Text and video generation require model access. No automatic social posts during development. Video quality requires human preview; generated on-screen lettering is not promised. Keep detailed tips and links in editable network captions. A 12-second storyboard is one multi-shot render, avoiding extra stitching infrastructure and preserving character continuity.

Sources: https://developers.openai.com/api/docs/guides/video-generation ; https://developers.facebook.com/docs/video-api/guides/reels-publishing/ ; https://developers.facebook.com/docs/instagram-platform/content-publishing/

## Implemented and verified

- Added AI Reel studio to social navigation and the existing format chooser. Plan → edit → save → generate → preview/download → schedule. Status checks run every 20 seconds while the page is visible, with a manual check control and a 15-second server throttle.
- Persisted provider jobs, MP4 data, and per-network upload/publication checkpoints. Signed, expiring MP4 URLs support byte ranges for Meta fetching and browser scrubbing. List/report queries defer video bytes.
- Provider errors do not expose raw responses or credentials. Explicit rejected renders can be retried; uncertain paid submissions are held. Active renders cannot be deleted. Unknown final publication responses are held for reconciliation rather than reposted.
- Facebook encoding or Instagram container processing can outlast the publishing attempt. The post moves to Needs attention with a retry/check control, which resumes the saved upload. For uncertain Instagram final publication, support must check the connected account/provider and reconcile the saved `reel_publications` entry before another submission. An uncertain generation similarly requires reconciling `reel_job` with the OpenAI project; never clear it blindly.
- Validation: 285 Django tests passed (social, blog, marketing); 41 browser tests passed across the social suites including 390px/1280px Reel checks. Django system checks, migration drift check, strict typing, lint, formatting and diff checks passed.
- No paid image/video request or live social publication was made. Production model access, rendered video quality, actual Meta publishing and deployment remain unverified. Apply migration 0009 and deploy web/publishing services together before use.

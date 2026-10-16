# Story-driven carousels and AI Reels

## Scope and acceptance criteria

1. Carousel planning preserves explicit objects and emotions, and assigns distinct story beats rather than repeating teaching scenes. The tablet example must include a disengaged child and tablet, a request for human connection, and shared reading. Respectful frustration is allowed.
2. A separate Reel composer plans an editable three-beat moving story, writes Facebook/Instagram captions, and generates an 8-second 9:16 MP4 using Google Veo 3.1. No uploaded human reference images. Fictional characters only. User selected actual AI-generated moving video, not animated photos.
3. Generation is asynchronous and explicitly started after storyboard review. Saved job IDs survive navigation; status checks never create another paid job. Ambiguous submissions are held for manual investigation. Completed MP4s persist with the draft and can be previewed/downloaded before scheduling.
4. Existing scheduling/publishing supports Reels on Facebook and Instagram. Provider upload/container IDs persist before subsequent steps. Ambiguous final submissions are held for reconciliation rather than blindly repeated. Photo/carousel publishing remains unchanged.
5. Mocked tests cover prompts, generation, invalid responses, provider errors, permissions, content serving, and publishing checkpoints. Run Django checks, migrations check, focused tests, lint, and typing.

## Delivery and limitations

Use SOCIAL_GEMINI_API_KEY from a paid Gemini project with Veo access. SOCIAL_VEO_MODEL defaults to veo-3.1-generate-preview. The existing OpenAI key is used only for storyboards and captions. Each explicit generation is billable; do not make paid production calls during development. Text and video generation require model access. No automatic social posts during development. Video quality requires human preview; generated on-screen lettering is not promised. Keep detailed tips and links in editable network captions. An 8-second storyboard is one multi-shot render, avoiding extra stitching infrastructure and preserving character continuity.

Sources: https://developers.openai.com/api/docs/guides/video-generation ; https://developers.facebook.com/docs/video-api/guides/reels-publishing/ ; https://developers.facebook.com/docs/instagram-platform/content-publishing/

## Implemented and verified

- Added AI Reel studio to social navigation and the existing format chooser. Plan → edit → save → generate → preview/download → schedule. Status checks run every 20 seconds while the page is visible, with a manual check control and a 15-second server throttle.
- Persisted provider jobs, MP4 data, and per-network upload/publication checkpoints. Signed, expiring MP4 URLs support byte ranges for Meta fetching and browser scrubbing. List/report queries defer video bytes.
- Provider errors do not expose raw responses or credentials. Explicit rejected renders can be retried; uncertain paid submissions are held. Active renders cannot be deleted. Unknown final publication responses are held for reconciliation rather than reposted.
- Facebook encoding or Instagram container processing can outlast the publishing attempt. The post moves to Needs attention with a retry/check control, which resumes the saved upload. For uncertain Instagram final publication, support must check the connected account/provider and reconcile the saved `reel_publications` entry before another submission. An uncertain generation similarly requires reconciling `reel_job` with the OpenAI project; never clear it blindly.
- Validation: 285 Django tests passed (social, blog, marketing); 41 browser tests passed across the social suites including 390px/1280px Reel checks. Django system checks, migration drift check, strict typing, lint, formatting and diff checks passed.
- No paid image/video request or live social publication was made. Production model access, rendered video quality, actual Meta publishing and deployment remain unverified. Apply migration 0009 and deploy web/publishing services together before use.


## October 16 provider correction

OpenAI's official deprecation notice records the Sora/Videos API shutdown on September 24, 2026. Production returned empty 404 responses for GET and deliberately invalid POST requests regardless of JSON, multipart, or form encoding. Model-list visibility did not imply a working render endpoint. The Sora transport has been removed, and Google Veo now handles creation, polling, and downloads. No automatic cross-provider retry or new paid generation is performed. Old in-progress Sora jobs become a clear failed state instead of being submitted to Google.

Veo renders are 8 seconds, 9:16, 720p, with native audio and fictional people. Downloads validate Google hosts, do not forward the API key across download redirects, and enforce the MP4 and 64 MB limits. Missing credentials disable generation before modifying the saved draft. The prompt/caption tools remain available.

Deployment requires SOCIAL_GEMINI_API_KEY; no such key existed in the inspected production configuration. Add it through Railway server variables, never through captions or chat. A new provider key, billing and regional eligibility remain required before an actual render can be verified.

Sources: https://developers.openai.com/api/docs/deprecations#2026-03-24-sora-2-video-generation-models-and-videos-api and https://ai.google.dev/gemini-api/docs/veo

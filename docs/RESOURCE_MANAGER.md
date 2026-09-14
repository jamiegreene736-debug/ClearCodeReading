# Resource Manager

## Delivery plan

- Add a focused Resources CMS to the existing Django site: file/link/article entry, reusable assets, autosaved drafts and immutable revisions.
- Use stable resource URLs and separate draft/live/scheduled revisions; editing or restoring a revision never changes published content until publication.
- Give active staff author access to their own resources; super admins and users with `resources.publish_resource` can review and publish all resources. Explicit `resources.add_resource` permission also grants contributor access.
- Keep the existing family registration gate and enforce it on detail, preview, cover and download responses. Public resources can be explicitly selected by a publisher.
- Store bounded, validated assets in PostgreSQL, matching assessment audio. No public media URLs or ephemeral deployment filesystem. Lists never select asset bytes.
- Add search, filters, bulk draft upload, duplicate, archive/restore, version recovery and timed publication.
- Verify database constraints, request permissions, competing edits, protected downloads, scheduling, migration and browser workflows before PR/merge/deployment read-back.

## Editorial workflow

Open **Resources** in the signed-in portal, then **Add resource**. Upload a file (PDF, DOCX, PPTX, TXT, JPEG, PNG or WebP; maximum 10 MB), paste an HTTPS link or write an article. A title is suggested for uploads. Review title, description, audience and topic. Additional fields include grade, language, cover image/description, featured placement and search metadata. Plain text articles preserve paragraphs and cannot inject HTML.

Drafts save automatically after editing pauses, with a visible saved/error state. Preview includes the actual card and resource page, with desktop/mobile widths. Save explicitly also works without JavaScript. Publishers can publish now or schedule in the displayed UTC timezone. Contributors submit for review. Later edits do not change a live or scheduled revision; publishing or scheduling again explicitly replaces it. Cancel schedule preserves the current live version. Due schedules become visible on the next request without a background worker.

Replace a file in the editor to preserve the resource URL; earlier files remain available to revision history. Duplicate creates a new unpublished draft. Archive hides a resource; restore makes its previous publication eligible again. Restoring a revision only replaces the draft. Bulk upload creates up to 10 separate drafts with shared topic/audience, is atomic on validation failure, and uses a request token to avoid duplicate imports. The asset picker reuses existing files and the file library shows usage counts. Revision history is paginated. Asset deletion is intentionally unavailable while history retains references.

## Access and operations

Resources are shared public-site content, not school/tenant session documents. Resource routes reject non-public tenant schemas. Only active authorized staff can edit; public demo identities and demo-login sessions are excluded. Existing sessions must reauthenticate once through the staff sign-in before using resources; permissions are checked on every action and asset request. `publish_resource` can be assigned through the existing user/group administration. Contributors see only their own records/assets. Super admins can publish without an extra permission assignment. Ordinary staff contributors cannot bypass review by changing access settings.

Public visitors see explicitly public resource content. Family resources use the existing CRM signup session. This is lead-registration access, not paid membership or identity verification. All content/asset responses are private/no-store so a shared cache cannot leak unlocked resources. No resource bytes are exposed through `/media/`. External links are HTTPS-only, are never fetched by the server, and supported YouTube/Vimeo URLs become allowlisted embeds. Embedded third-party media loads only on the resource page.

Uploads are limited to 10 MB each, 10 files per batch, 50 MB total. Images are decoded and re-encoded; text and office document structures are checked. PDF first-page previews are rendered to images in a separate process with an eight-second timeout (and CPU/memory limits on Linux); other document formats download as attachments. PDF previews also supply automatic card thumbnails. Password-protected or unrenderable PDFs are rejected with a clear retry message. A bulk request has a 20-second preparation budget. Database asset storage is appropriate for this bounded initial library; monitor database growth and migrate the asset backend to private object storage if volume warrants it. File content is deduplicated without exposing other contributors' files. Deployment runs shared migrations through the existing Railway predeploy script.

## Verification

Automated coverage lives in `apps/resources/tests.py`; run it with the existing blog, CRM and user tests against local PostgreSQL. The browser smoke test in `scripts/test_resource_browser.py` uses a separate local-only test database/account and exercises article creation, autosave, preview, publishing, draft isolation, family signup return, mobile layout and file replacement. Install Playwright in the development environment, start Django on port 8876, and run the script with the same local database environment. It refuses a non-local database host. No production fixture content is published.

Current validation: 204 Django tests passed across resources, blog, CRM, CRM email and users (including hiring); responsive browser publishing flow passed with no JavaScript errors. Static collection, CSS build and migration drift checks passed. Strict mypy checks pass for all 16 production resource modules using `mypy-resources.ini` and the existing development typing dependencies.

### Initial staff access

Public demo identities cannot use the Resource Manager, including when signed in with their published demo password. A normal staff login records its trusted login origin; legacy sessions sign in once again. Operators can issue a private one-use, seven-day setup link with `python manage.py create_resource_setup_link`. The recipient chooses their own email and password. The new account receives only the resource publishing permission, not superuser or general staff privileges. Tokens are hashed in the database, consumed under a row lock and never exposed in the manager. Share the link only with the intended website owner.

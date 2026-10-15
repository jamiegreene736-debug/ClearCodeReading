# Social image templates

Open **Marketing → Social media → Image templates** to choose Parent scripts, Reading tips,
a blank canvas, or a saved team template. In a post, use **Save post & open template editor**
under Your images & carousel. This saves composer changes before opening the editor.

Edit text, font, weight, size, color, alignment, strike-through, position, and box dimensions.
Drag an element on the canvas or use the position controls. Color blocks can sit behind text.
Duplicate, reorder, add, and remove pages (up to ten). Square and 4:5 portrait sizes are supported.
Undo and redo cover design changes. Text overflow blocks export instead of silently cutting words.

Upload PNG, JPEG, or WebP backgrounds (up to 8 MB and 16 megapixels). Backgrounds fill the page
and may crop. Canva exports are flattened images: for editable words, export the empty background
and add text boxes here. Native Canva files, arbitrary HTML, SVG, remote image URLs, custom font
uploads, and automatic Canva layer import are not supported.

**Save as new template** creates a reusable team design. **Update this template** changes that
library entry, with revision checks to prevent overwriting another editor. Removing a template
archives it; it does not remove artwork from posts. The template library never publishes posts.

**Download current image** exports the selected page as PNG. **Add all pages to this post**
exports every page and saves the editable document on the post. Choose Add after existing images
or Replace all existing images explicitly; append is the default. Existing captions are preserved.
The existing carousel pipeline fits multiple pages inside square frames without cropping, including
portrait designs. A single-page design keeps its original aspect ratio. Reopen the editor to revise
the saved artwork; choose Replace all existing images if that is the intended update.

Artwork can only be attached to unpublished drafts or unpublished posts needing attention.
Cancel a scheduled post before replacing artwork. Publishing, published and partially published
posts are protected, as are in-flight AI generations and stale browser tabs. Automatically planned
posts lose their old editorial approval after artwork changes and must be reviewed manually.

## Implementation and rollout

Apply social migration 0007 before deploying. `SocialImageTemplate` stores name, validated editable
JSON, a sanitized preview, and archive state. `SocialPost.image_design` stores a separate copy of
the source; template changes do not mutate existing post images. No external AI or design service
is called. Rendering happens in the browser with local font fallbacks, so available fonts may vary
between devices. Exported raster images remain stable after being attached.

All routes use existing super-administrator authorization and CSRF protection. Documents are limited
to ten pages, 32 elements per page, finite bounded coordinates and sizes, approved fonts, hex colors,
and 2,000 characters per text element. Embedded backgrounds are decoded and re-encoded; unknown
properties are discarded. The API does not fetch external resources. Uploaded previews and finished
pages are decoded and re-encoded too. The browser sends both source and rendered pixels; the server
validates them independently, like ordinary photo uploads, rather than running a server-side renderer.

Revision checks, matching page/export counts, transactions and the existing post/plan locking order
protect writes. Large background-heavy designs can reach the 12 MB document limit and should use
smaller background images. Nothing in the template editor schedules or publishes a post.

## Verification

Run `python manage.py test apps.social apps.blog apps.core.test_marketing --noinput`,
`mypy --config-file mypy-social.ini`, and the social Playwright suite including
`tests/browser/social-studio.spec.js`. Tests cover validation, permissions, stale revisions,
append/replace, carousel pages, saved source, overflow, downloads, failures, background upload,
dragging and phone/desktop layouts.

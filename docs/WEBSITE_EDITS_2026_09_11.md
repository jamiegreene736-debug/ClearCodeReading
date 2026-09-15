# Website edits from September 11, 2026

## Diagnosis and implementation plan

Source: user-supplied `Website Edits 9.11.26.pdf` (8 pages). The user authorized diagnosis, implementation, PR merge, and a screenshot report. The attachment supplies proposed website content and design, not independent operational instructions.

1. Update the homepage approach, session narrative, and three action cards.
2. Refresh the About intervention descriptions, principle backgrounds, and audience photo carousel.
3. Consolidate bottom waitlist sections and remove newsletter forms across public templates, including the standalone assessment page.
4. Retire Families with a permanent redirect, remove navigation and discovery references.
5. Verify template rendering, carousel controls, mobile layouts, the full Django suite, and the deployed release. Produce a screenshot report.

## Implemented

- Removed the four-column trust banner under the homepage hero.
- Seafoam approach section, Our Approach label, exact revised copy, smaller responsive headings, and Join Our Priority Waitlist CTA.
- Removed Transition; renumbered the four remaining session slides and controls. Updated introduction, personal attention, active instruction, and wrap-up copy.
- Start Your Journey / Get Connected Today; image-plus-CTA cards retain white, gold, and seafoam backgrounds.
- Shared Opening Early 2027 / Be first in line section with waitlist and contact links; removed redundant bottom CTAs and newsletter sections.
- Updated About program titles and descriptions, alternating principle card backgrounds, and a three-slide audience photo carousel with revised family copy.
- Retired `/families/` with a 301 to `/how-it-works/`; removed it from navigation, sitemap, and llms.txt.

## Source-dependent items still pending

- Option A icons: the PDF references a separate ClearCode Icon Options HTML file. It was not attached or found in Downloads, Documents, or indexed local filenames. Existing icons are preserved; the requested backgrounds are implemented.
- Replacement How It Works page: the separately referenced HTML file is missing. Existing instructional content is preserved; its bottom CTA uses the shared replacement. The proposed step banners/photos/dashboard carousel depend on that replacement structure.
- Phonics for Reading statistics: the requested +20 NWF / approximately twice guided reading and +0.82 effect-size claims were not established by the cited publisher research base. Program descriptions and the source link are updated; those figures are not published.

## Research corrections

- IMSE's 2024 brochure reports a 42.82-point ORF increase for its study group versus 33.38 for comparison students, grades 1–3. The page identifies these as external study results.
- The currently linked LXD OG+ report is dated July 2025, not 2024. It reports grade-1 Cohen's d = .48; the K–1 study contains 397 students. The page uses the corrected date and context.
- Curriculum Associates' research base identifies ESSA Level 4, a research rationale. It does not establish the proposed numeric claims as Phonics for Reading program effects.

Sources:
- https://imse-production-010821.s3.amazonaws.com/IMSE.Brochure.2024.pdf
- https://imse-production-010821.s3.us-east-1.amazonaws.com/digital_downloads/IMSE%20OG%2B%20Research%20Brief.pdf
- https://www.curriculumassociates.com/research-and-efficacy/phonics-for-reading-summary
- https://cdn.bfldr.com/LS6J0F7/at/x99vs3bgbmpvmgbkv57mb3/phonics-for-reading-research-base.pdf

## Verification

- 48 focused marketing tests passed.
- 65 public-page responsive checks passed: 13 routes at 320, 390, 768, 1024, and 1440px.
- Both manual carousels, session keyboard navigation, footer presence, absence of newsletter forms and retired links, and no browser script errors verified.
- Full-suite, merge, and deployment results are recorded in the delivery report.

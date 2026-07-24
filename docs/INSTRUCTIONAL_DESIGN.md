# ClearCode Reading Instructional Design Lock

Status: **Phase 0 authoritative**

Schema version: **2026.1**
Applies to: **PRD FR-0.1, FR-1.2, FR-2.1, FR-5.1**

This document freezes the instructional decisions and data contract used by placement,
session capture, later decision support, and outcomes reporting. Publisher page numbers,
word lists, and item identifiers are edition-specific licensed content. They belong in
`CurriculumSequence.metadata.publisher_crosswalk`, never in application logic.

## 1. Methodology decision

ClearCode supports exactly two intervention methodologies:

1. Phonics for Reading (PFR), Levels A, B, and C.
2. IMSE Comprehensive Orton-Gillingham Plus (OG+).

A child has one active `StudentPlacement` and therefore one methodology at a time.
Instruction must not blend PFR and OG+ within a placement or session. A specialist may
change methodology only by closing the active placement, creating a new placement, and
recording the rationale and supporting evidence. Moving within a methodology uses a
`StudentPlacementOverride` so the previous and new positions remain queryable.

The existing digital Reading Survey remains supporting context. It cannot choose a
methodology or position without the methodology-specific placement evidence below.

## 2. Graph contract

Every graph position has a stable ClearCode code, versioned curriculum, center, order,
prerequisites, instructional targets, structured item-set schema, and mastery criteria.
Historical records continue to point to the version used at the time of instruction.

### 2.1 PFR canonical scope and sequence

PFR is a linear graph within each level. Each lesson maps letter/sound knowledge to word
types, syllable types, high-frequency words, encoding, and connected text. Every full
lesson is delivered across two 60-minute captures:

- **Session 1a:** phonemic-awareness warm-up, sound drill, explicit pattern instruction,
  word reading, and encoding.
- **Session 1b:** cumulative review, a different word/item set, high-frequency-word
  practice, connected text, fluency, and application.

The following 32-position level map is the ClearCode canonical graph. A center's licensed
edition crosswalk supplies the exact publisher lesson and item references.

| Lesson | Level A target | Level B target | Level C target |
|---:|---|---|---|
| 1 | short `a`; m, s, t; VC/CVC | closed-syllable review | six-syllable-type review |
| 2 | short `i`; f, n, p; VC/CVC | short-vowel contrasts | closed/open contrasts |
| 3 | short `o`; c, h; CVC | digraph review | VCe/vowel-team contrasts |
| 4 | short `e`; d, r; CVC | blends and digraphs | r-controlled review |
| 5 | short `u`; b, g; CVC | `ck`, `tch`, `dge` | variant vowel spellings |
| 6 | l, k, j, w; CVC | VCe long `a` | advanced `oi`/`oy` |
| 7 | v, x, y, z, `qu`; CVC | VCe long `i` | advanced `ou`/`ow` |
| 8 | continuous-sound CVC blending | VCe long `o` | `aw`/`au`/`augh` |
| 9 | stop-sound CVC blending | VCe long `u` | `oo` sound alternatives |
| 10 | `sh`, `ch`; CVC/CCVC | VCe cumulative contrast | ambiguous vowel teams |
| 11 | `th`, `wh`; CVC/CCVC | long-a teams | hard/soft `c` |
| 12 | final `ck`; CVC | long-e teams | hard/soft `g` |
| 13 | final `ng`, `nk`; CVCC | long-i teams | silent-letter patterns |
| 14 | final consonant blends; CVCC | long-o teams | `-tion`, `-sion`, `-cian` |
| 15 | initial s-blends; CCVC | long-u teams | consonant-le review |
| 16 | initial l-blends; CCVC | vowel-team cumulative review | stable final syllables |
| 17 | initial r-blends; CCVC | r-controlled `ar` | prefixes: `un-`, `re-`, `pre-` |
| 18 | plural/third-person `-s` | r-controlled `or` | prefixes: `dis-`, `mis-`, `non-` |
| 19 | inflection `-ing` | r-controlled `er`, `ir`, `ur` | suffixes: `-ful`, `-less`, `-ness` |
| 20 | inflection `-ed` | r-controlled cumulative review | suffixes: `-ment`, `-ly`, `-er` |
| 21 | closed-syllable review | diphthongs `oi`, `oy` | suffixes: `-tion`, `-sion` |
| 22 | mixed CVC/CCVC/CVCC | diphthongs `ou`, `ow` | inflection and spelling changes |
| 23 | two-consonant blends | variant `oo` sounds | three great rules review |
| 24 | three-consonant blends | `aw`, `au` | roots and base-word recognition |
| 25 | compound words | open syllables | two-syllable morphology |
| 26 | two closed syllables | V/CV and VC/V division | three-syllable decoding |
| 27 | syllable division practice | VCCV and VCV division | schwa in unstressed syllables |
| 28 | cumulative high-frequency words | consonant-le syllables | Greek/Latin combining forms |
| 29 | phrase reading | common prefixes | academic multisyllabic words |
| 30 | sentence and passage accuracy | common suffixes | connected-text transfer |
| 31 | cumulative lesson check | multisyllabic application | cumulative challenge text |
| 32 | level review and promotion check | level review and promotion check | level review and exit check |

The high-frequency-word strand is position-bound, not a detached list. For the initial
Level A seed, Lessons 1–20 introduce these pairs in order: `the/a`, `I/is`, `to/and`,
`you/of`, `was/said`, `he/she`, `we/they`, `are/were`, `have/has`, `do/does`,
`come/some`, `one/once`, `two/who`, `what/when`, `where/why`, `there/here`,
`could/would`, `should/again`, `because/before`, and `after/through`. Licensed-edition
crosswalks may replace a pair for a new curriculum version; historical positions are not
rewritten. Levels B and C attach their edition-specific cumulative word sets to every
lesson position and require previously introduced words in Session 1b review.

Each PFR position stores:

- `letter_sounds`: grapheme/phoneme targets.
- `word_types`: CVC, CCVC, CVCC, VCe, affixed, or multisyllabic patterns.
- `syllable_types`: applicable members of the six-type taxonomy.
- `high_frequency_words`: edition-specific words assigned to that lesson.
- `item_set_schema.session_1a` and `.session_1b`: distinct item identifiers.
- `prerequisites`: normally the prior lesson plus any required earlier pattern.

### 2.2 OG+ canonical concept graph

OG+ uses concept positions rather than PFR lessons. The six syllable types are **closed,
open, vowel-consonant-e, vowel team, r-controlled, and consonant-le**. Four division
patterns are stored in position metadata: VCCV, VCV, V/V, and consonant-le.

The graph contains 83 stable concept positions:

| Concepts | Canonical instructional targets |
|---|---|
| 1–4 | rhyme/first sound; onset-rime; phoneme blending; phoneme segmenting |
| 5–9 | short `a`; c; o; g; d |
| 10–14 | t; m; l; h; short `i` |
| 15–19 | p; n; f; s; short `o` |
| 20–24 | r; k; b; j; short `e` |
| 25–29 | v; w; x; y; z |
| 30–33 | short `u`; `qu`; mixed closed syllables; closed-syllable checkpoint |
| 34–37 | digraphs `sh`, `ch`, `th`, `wh` |
| 38–41 | final `ck`; FLOSS; `tch`; `dge` |
| 42–45 | initial blends; final blends; three-letter blends; blend checkpoint |
| 46–49 | VCe long `a`, long `i`, long `o`, long `u` |
| 50–53 | open syllables; V/CV division; VC/V division; VCe/open checkpoint |
| 54–58 | vowel teams long `a`, long `e`, long `i`, long `o`, long `u` |
| 59–62 | r-controlled `ar`, `or`, `er`, `ir`/`ur` |
| 63–66 | diphthongs `oi`/`oy`, `ou`/`ow`, variant `oo`, `aw`/`au` |
| 67–69 | VCCV division; V/V division; consonant-le syllables |
| 70–72 | soft `c`; soft `g`; silent-letter patterns |
| 73–75 | inflectional `-s`/`-es`; `-ing`; `-ed` |
| 76–78 | doubling rule; silent-e dropping rule; y-to-i rule |
| 79–81 | common prefixes; common suffixes; base words and roots |
| 82 | multisyllabic decoding, encoding, and connected-text transfer |
| 83 | cumulative concept review and methodology completion check |

The “three great rules” are captured as distinct concepts:

1. **One-one-one doubling:** double the final consonant before a vowel suffix when the
   one-syllable base ends in one vowel followed by one consonant.
2. **Silent-e dropping:** drop final silent `e` before a vowel suffix, retaining it where
   required to preserve sound or meaning.
3. **Y-to-i:** change consonant + `y` to `i` before most non-`i` suffixes.

Each concept may carry two separate red-word arrays:

- `red_words_spell_and_read`: the learner must encode and decode the word.
- `red_words_read_only`: automatic recognition is required; encoding is not a promotion
  gate at that position.

Red words never substitute for decodable pattern practice. Licensed edition word lists
are stored in the curriculum crosswalk, not copied into source code.

## 3. Deterministic placement

Placement calculations must store raw item outcomes, assessment version, administrator,
date, computed recommendation, final position, and any specialist override.

### 3.1 PFR Placement Test

1. Start at the entry point selected by grade/age context and the publisher's routing
   table; store that starting part.
2. Allow **five seconds per word**. A response after five seconds is incorrect and stores
   `timeout=true`.
3. Stop the current part after **four consecutive errors**. Unadministered items are
   `not_reached`, never incorrect.
4. For multisyllabic items, score every published part independently. The word is correct
   only when all required parts are correct, while per-part scores remain queryable.
5. A part is passed at **80% or greater** among administered scorable items.
6. **Basal:** the earliest tested part at or above 80% with the required preceding part
   also at or above 80%; when testing begins at the first part, that first passing part is
   the basal.
7. **Ceiling:** the first part below 80% or terminated by four consecutive errors.
8. Place at the first lesson whose prerequisite skill corresponds to the ceiling. If all
   tested parts pass, place at the next unmastered lesson or record a PFR completion
   recommendation. If no basal is established, place at Level A Lesson 1.
9. Re-running the algorithm over stored item results must produce the same recommendation.

The digital Reading Survey may be displayed beside these results but does not alter the
calculation.

### 3.2 OG+ placement

Use the learner's instructional grade band at administration:

- **Kindergarten:** Phonological Awareness Diagnostic plus Beginning Reading Skills
  evidence; reassess at the configured midyear checkpoint.
- **Grades 1–2:** grade-band initial Benchmark Assessment, with midyear and final
  benchmarks. Begin at the first concept not demonstrated.
- **Grade 3+:** Informal Spelling Survey. Store each phonological, orthographic, and
  morphological error category, then place at the earliest prerequisite concept linked
  to a repeated error category.
- **All bands:** Pause-to-Assess checkpoints confirm retention before advancement.

For a reproducible recommendation:

1. Score each concept-linked item exactly as published and retain the raw result.
2. A concept is demonstrated when decoding is at least 90% and, where assessed, encoding
   is at least 85%.
3. Scan concepts in graph order. Place at the earliest concept that is not demonstrated
   after the last fully demonstrated prerequisite chain.
4. If evidence is incomplete or internally inconsistent, return `specialist_review`
   rather than guessing.
5. A specialist override requires previous position, new position, rationale, specialist,
   timestamp, and the evidence considered.

## 4. Mastery and promotion

### 4.1 PFR

A lesson is promoted only when:

- word reading is at least **90%** on two distinct item sets;
- encoding is at least **85%** where the lesson includes encoding;
- connected-text accuracy is at least **95%** on Session 1b;
- required high-frequency words are at least **90%**;
- both Session 1a and Session 1b are complete; and
- no required error-pattern category appears in both latest checks above its configured
  tolerance.

If one criterion misses, repeat only the affected routine/item set and keep the placement
at the lesson. Level promotion additionally requires the level review at **90% overall**
with no required component below **80%**.

### 4.2 OG+

Promote a concept when two checks using different items show:

- decoding at least **90%**;
- encoding at least **85%** when applicable;
- phoneme manipulation at least **80%** when applicable;
- red words marked spell-and-read at least **90%** in both modes;
- red words marked read-only at least **95%** in reading; and
- connected-text application at least **95% accuracy** when included.

Pause-to-Assess checkpoints require **90% overall**, no concept cluster below **80%**, and
all prerequisite concepts mastered. A checkpoint miss routes to the earliest missed
prerequisite, not automatically to the immediately previous concept.

## 5. Low-growth and review flags

Flags are deterministic and may coexist:

| Code | Definition | Required response |
|---|---|---|
| `three_reteach_sessions` | Re-teach recorded for the same position in three consecutive completed sessions | Specialist reviews item-level patterns before session four |
| `flat_accuracy` | Less than 5 percentage-point gain across four completed captures at one position | Review grouping, pacing, and item variation |
| `mastery_time_outlier` | Completed sessions at a position exceed 150% of the curriculum median, with at least four sessions | Review prerequisite evidence |
| `regression_after_mastery` | Two later checks fall below the promotion threshold for a mastered position | Add cumulative review; do not erase prior mastery |
| `error_pattern_persistent` | Same structured error code appears in three consecutive completed captures | Select a targeted routine and new item set |
| `attendance_interruption` | More than 14 days between completed sessions while a position is in progress | Recheck retained prerequisites |

Flags indicate a need for instructional review. They do not label a learner.

## 6. Session capture contract

`Session` remains the source capture record. Its structured fields are primary and
`notes` is supplemental. `SessionTemplate` selects the intervention-specific capture
contract, and `SkillObservation` is the canonical queryable projection of completed
session evidence.

### 6.1 Common required fields

- center, child, specialist, curriculum position, all targeted positions;
- scheduled start, actual start/end, status, and intervention part;
- activities completed with activity code, completion status, minutes, and item-set ID;
- accuracy rate plus numerator and denominator inside the relevant item set;
- time-to-mastery signals;
- structured error patterns;
- structured behavioral observations;
- next-session direction;
- home-practice suggestion;
- revision, editor, and immutable revision snapshot.

Recommended JSON shapes:

```json
{
  "activities_completed": [
    {
      "code": "word_reading",
      "status": "completed",
      "minutes": 12,
      "item_set_id": "PFR-A-08-1A-WR-01"
    }
  ],
  "time_to_mastery_signals": {
    "cumulative_sessions_at_position": 2,
    "first_attempt_accuracy": 78.0,
    "latest_accuracy": 91.0,
    "prompts_per_10_items": 1,
    "independent_transfer": true,
    "reteach": false
  },
  "error_patterns": [
    {
      "code": "short_vowel_confusion",
      "target": "a_to_e",
      "count": 3,
      "opportunities": 12,
      "item_ids": ["item-2", "item-7", "item-9"]
    }
  ],
  "behavioral_observations": [
    {
      "code": "task_persistence",
      "rating": "consistent",
      "activity_code": "word_reading"
    }
  ]
}
```

Allowed behavioral codes describe observable participation only: `task_persistence`,
`attention_to_print`, `response_latency`, `self_correction`, `requests_break`,
`uses_strategy`, and `confidence_to_attempt`. Allowed ratings are `rare`, `emerging`,
`inconsistent`, and `consistent`.

### 6.2 PFR-specific capture

`intervention_part` must be `pfr_1a` or `pfr_1b`. Both captures point to the same lesson
position. `item_sets` must identify distinct items for each part and preserve item-level
correctness, latency/timeout, prompt level, and encoding/decoding mode. A repeated part
uses a new item-set ID.

### 6.3 OG+-specific capture

`intervention_part` must be `og_concept`. Item sets identify review concepts, the new
concept, three-part drill items, encoding items, red words by category, connected text,
and dictation where used. Every item links to a `CurriculumSequence` concept so later
prerequisite and error-pattern analysis requires no additional collection.

### 6.4 Session templates and skill observations

`SessionTemplate` closes the Technical Spec §5.5 gap. A versioned, center-scoped
template belongs to one curriculum and may be narrowed to one `CurriculumSequence`
lesson or concept. The session defaults endpoint resolves an exact-position template
before a curriculum-wide fallback and returns its `capture_fields` schema and form
defaults. Completed sessions validate against the pinned template version, so PFR
Session 1a, PFR Session 1b, and OG+ concept capture do not share a universal form.
Curricula without a configured template retain the existing session contract while
templates are added.

`SkillObservation` closes the Technical Spec §6.1 gap. On session completion, the
structured activities, item references, accuracy, response rating when present, error
pattern tags, and timing signals are synchronized into one center- and child-scoped
record per observed `CurriculumSequence` position. Editing a completed session updates
the same observation and records the source session revision; moving a session out of
completed status makes its observations inactive. Existing Session JSON fields and API
inputs remain in place for compatibility.

The observation API can filter by center, child, session, or curriculum position.
Prediction, instructional flagging, and outcome aggregation can therefore query stable
rows without asking specialists or families to collect new information.

## 7. Language policy

All user-facing labels, reports, exports, and editable field help text use instructional
language. Prohibited clinical terms are **diagnosis**, **treatment**, and **therapy**.
Use **instructional assessment**, **intervention**, **instructional plan**, **skill
pattern**, **learning need**, and **specialist review** instead.

The platform is education-only and FERPA-aligned. Product copy must not imply medical
care, medical records, or a clinical provider relationship.

## 8. Data sufficiency for later capabilities

Prediction can use the versioned graph, prerequisites, placement evidence, item-level
accuracy, errors, timing, prompting, cumulative attempts, and observation codes.
Outcomes can compare entry position, position velocity, mastery checks, retention,
connected-text transfer, attendance gaps, and methodology version by center and
specialist. No additional session fields are required for those capabilities; later work
adds calculations and presentation only.

### 8.1 Technical Spec domain entities

- **§5.2 — `SkillCrosswalk`:** versioned center or global mappings support common-scale
  reporting and methodology-transition review. Crosswalks never populate or modify an
  active placement, sequence plan, group, or session.
- **§5.4 — `SequencePlan`:** confirmation may materialize the ranked
  `RecommendedSequencePosition` rows into ordered `SequencePlanItem` records. The
  recommendation remains the decision artifact; the plan is the specialist's working
  artifact with pending, in-progress, mastered, and skipped item states. Every item must
  use the placement's exact curriculum version.
- **§5.6 — `Group`:** an instructional group belongs to one center and one exact
  curriculum, has an approximate sequence range, and admits only students whose active
  placement uses that curriculum and falls in that range. Optimizer output is advisory;
  operations staff create or update the group explicitly.

## 9. Source governance

- Curriculum Associates describes PFR as three levels with embedded placement and
  progress monitoring:
  <https://www.curriculumassociates.com/programs/i-ready-learning/phonics-for-reading>
- IMSE documents K–2 benchmark use and Grade 3+ spelling analysis:
  <https://ogsupport.imse.com/docs/lessons/assessment/assessing-your-students/>
- IMSE identifies 83 K–2 spelling concepts and concept-aligned red words:
  <https://imse.com/products/Comprehensive-Orton-Gillingham-Plus-Words-and-Sentences-PDF/>

The instructional-design owner must approve a new `Curriculum.version` and publisher
crosswalk before an edition change is activated. Existing graph rows and session history
are never rewritten to mimic a newer edition.

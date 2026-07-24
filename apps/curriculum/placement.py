from dataclasses import dataclass
from typing import Iterable

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ai.services import InstructionalAIError, get_instructional_ai_service
from apps.curriculum.models import (
    Curriculum,
    PlacementEvidence,
    PlacementRecommendation,
    RecommendedSequencePosition,
    SequencePlan,
    SequencePlanItem,
    StudentPlacement,
    StudentPlacementOverride,
)


@dataclass(frozen=True)
class PlacementDecision:
    decision: str
    position_code: str | None
    deficit_profile: list[dict]
    rule_trace: dict
    rationale: str


def _percent(correct, total):
    if not isinstance(correct, int) or not isinstance(total, int) or total <= 0 or correct < 0 or correct > total:
        raise ValueError("Scores require integer correct and total values with 0 <= correct <= total.")
    return round((correct / total) * 100, 2)


def score_pfr_placement(raw_results: dict, ordered_codes: Iterable[str]) -> PlacementDecision:
    ordered_codes = list(ordered_codes)
    parts = raw_results.get("parts")
    if not isinstance(parts, list) or not parts:
        return PlacementDecision(
            PlacementRecommendation.Decision.SPECIALIST_REVIEW,
            None,
            [],
            {"reason": "missing_parts"},
            "Structured PFR part results are required before placement can be calculated.",
        )

    scored_parts = []
    known_codes = set(ordered_codes)
    for part in parts:
        code = part.get("position_code") if isinstance(part, dict) else None
        items = part.get("items") if isinstance(part, dict) else None
        if code not in known_codes or not isinstance(items, list) or not items:
            return PlacementDecision(
                PlacementRecommendation.Decision.SPECIALIST_REVIEW,
                None,
                [],
                {"reason": "invalid_part", "position_code": code},
                "A PFR part is incomplete or does not match the active curriculum version.",
            )

        administered = 0
        correct = 0
        consecutive_errors = 0
        terminated = False
        item_trace = []
        for item in items:
            if not isinstance(item, dict):
                return PlacementDecision(
                    PlacementRecommendation.Decision.SPECIALIST_REVIEW,
                    None,
                    [],
                    {"reason": "invalid_item", "position_code": code},
                    "Every PFR item outcome must be a structured object.",
                )
            if item.get("status") == "not_reached" or terminated:
                item_trace.append({"item_id": item.get("item_id"), "status": "not_reached"})
                continue
            if "correct" not in item:
                return PlacementDecision(
                    PlacementRecommendation.Decision.SPECIALIST_REVIEW,
                    None,
                    [],
                    {"reason": "missing_item_outcome", "position_code": code},
                    "Every administered PFR item needs a correctness outcome.",
                )
            administered += 1
            timed_out = bool(item.get("timeout")) or float(item.get("latency_seconds", 0) or 0) > 5
            part_outcomes = item.get("parts")
            parts_correct = True
            if part_outcomes is not None:
                if not isinstance(part_outcomes, list) or not part_outcomes or any(
                    not isinstance(part, dict) or "correct" not in part
                    for part in part_outcomes
                ):
                    return PlacementDecision(
                        PlacementRecommendation.Decision.SPECIALIST_REVIEW,
                        None,
                        [],
                        {"reason": "invalid_multisyllabic_parts", "position_code": code},
                        "Every multisyllabic part needs an independent correctness outcome.",
                    )
                parts_correct = all(bool(part["correct"]) for part in part_outcomes)
            is_correct = bool(item["correct"]) and parts_correct and not timed_out
            correct += int(is_correct)
            consecutive_errors = 0 if is_correct else consecutive_errors + 1
            item_trace.append(
                {
                    "item_id": item.get("item_id"),
                    "correct": is_correct,
                    "timeout": timed_out,
                    "parts": part_outcomes or [],
                }
            )
            if consecutive_errors >= 4:
                terminated = True

        if administered == 0:
            return PlacementDecision(
                PlacementRecommendation.Decision.SPECIALIST_REVIEW,
                None,
                [],
                {"reason": "no_scorable_items", "position_code": code},
                "A tested PFR part must include at least one administered item.",
            )
        accuracy = round((correct / administered) * 100, 2)
        scored_parts.append(
            {
                "position_code": code,
                "correct": correct,
                "administered": administered,
                "accuracy": accuracy,
                "passed": accuracy >= 80,
                "terminated_after_four_errors": terminated,
                "items": item_trace,
            }
        )

    basal = None
    for index, part in enumerate(scored_parts):
        if part["passed"] and (index == 0 or scored_parts[index - 1]["passed"]):
            basal = part["position_code"]
            break
    ceiling = next((part for part in scored_parts if not part["passed"] or part["terminated_after_four_errors"]), None)

    if basal is None:
        position_code = ordered_codes[0] if ordered_codes else None
        return PlacementDecision(
            PlacementRecommendation.Decision.PLACE if position_code else PlacementRecommendation.Decision.SPECIALIST_REVIEW,
            position_code,
            [{"code": "no_basal", "position_code": position_code}],
            {"parts": scored_parts, "basal": None, "ceiling": ceiling and ceiling["position_code"]},
            "No basal was established, so the frozen PFR rule returns to the first available lesson.",
        )

    if ceiling:
        position_code = ceiling["position_code"]
        return PlacementDecision(
            PlacementRecommendation.Decision.PLACE,
            position_code,
            [{"code": "pfr_part_below_80", "position_code": position_code, "accuracy": ceiling["accuracy"]}],
            {"parts": scored_parts, "basal": basal, "ceiling": position_code},
            f"Place at {position_code}, the first tested PFR part below the 80% passage threshold.",
        )

    last_index = ordered_codes.index(scored_parts[-1]["position_code"])
    if last_index + 1 >= len(ordered_codes):
        return PlacementDecision(
            PlacementRecommendation.Decision.CURRICULUM_COMPLETE,
            None,
            [],
            {"parts": scored_parts, "basal": basal, "ceiling": None},
            "All available tested PFR parts passed; specialist completion review is recommended.",
        )
    next_code = ordered_codes[last_index + 1]
    return PlacementDecision(
        PlacementRecommendation.Decision.PLACE,
        next_code,
        [],
        {"parts": scored_parts, "basal": basal, "ceiling": None},
        f"All tested parts passed; continue at the next unmastered lesson, {next_code}.",
    )


def score_og_placement(raw_results: dict, ordered_codes: Iterable[str], instrument: str) -> PlacementDecision:
    ordered_codes = list(ordered_codes)
    if instrument == PlacementEvidence.Instrument.OG_SPELLING_SURVEY:
        error_categories = raw_results.get("error_categories")
        if not isinstance(error_categories, list):
            return PlacementDecision(
                PlacementRecommendation.Decision.SPECIALIST_REVIEW,
                None,
                [],
                {"reason": "missing_error_categories"},
                "Structured spelling error categories are required for this placement instrument.",
            )
        repeated = []
        for category in error_categories:
            if not isinstance(category, dict) or not isinstance(category.get("count"), int):
                continue
            position_codes = category.get("position_codes", [])
            if category["count"] >= 2:
                repeated.extend(
                    {
                        "code": category.get("code", "repeated_spelling_pattern"),
                        "count": category["count"],
                        "position_code": code,
                    }
                    for code in position_codes
                    if code in ordered_codes
                )
        if not repeated:
            return PlacementDecision(
                PlacementRecommendation.Decision.SPECIALIST_REVIEW,
                None,
                [],
                {"reason": "no_repeated_mapped_error"},
                "No repeated error category maps reproducibly to the active OG+ graph.",
            )
        earliest = min(repeated, key=lambda gap: ordered_codes.index(gap["position_code"]))
        return PlacementDecision(
            PlacementRecommendation.Decision.PLACE,
            earliest["position_code"],
            repeated,
            {"repeated_error_categories": repeated},
            f"Place at {earliest['position_code']}, the earliest prerequisite linked to a repeated spelling pattern.",
        )

    concepts = raw_results.get("concepts")
    if not isinstance(concepts, list) or not concepts:
        return PlacementDecision(
            PlacementRecommendation.Decision.SPECIALIST_REVIEW,
            None,
            [],
            {"reason": "missing_concepts"},
            "Concept-linked OG+ item results are required before placement can be calculated.",
        )

    outcomes = []
    for concept in concepts:
        code = concept.get("position_code") if isinstance(concept, dict) else None
        decoding = concept.get("decoding") if isinstance(concept, dict) else None
        if code not in ordered_codes or not isinstance(decoding, dict):
            return PlacementDecision(
                PlacementRecommendation.Decision.SPECIALIST_REVIEW,
                None,
                [],
                {"reason": "incomplete_concept", "position_code": code},
                "OG+ evidence is incomplete or does not match the active curriculum graph.",
            )
        try:
            decoding_accuracy = _percent(decoding.get("correct"), decoding.get("total"))
            encoding = concept.get("encoding")
            encoding_accuracy = (
                _percent(encoding.get("correct"), encoding.get("total"))
                if isinstance(encoding, dict)
                else None
            )
        except ValueError:
            return PlacementDecision(
                PlacementRecommendation.Decision.SPECIALIST_REVIEW,
                None,
                [],
                {"reason": "invalid_score", "position_code": code},
                "OG+ item totals are incomplete or internally inconsistent.",
            )
        demonstrated = decoding_accuracy >= 90 and (encoding_accuracy is None or encoding_accuracy >= 85)
        outcomes.append(
            {
                "position_code": code,
                "decoding_accuracy": decoding_accuracy,
                "encoding_accuracy": encoding_accuracy,
                "demonstrated": demonstrated,
            }
        )

    first_gap = next((outcome for outcome in outcomes if not outcome["demonstrated"]), None)
    if first_gap:
        return PlacementDecision(
            PlacementRecommendation.Decision.PLACE,
            first_gap["position_code"],
            [{"code": "og_concept_not_demonstrated", **first_gap}],
            {"concepts": outcomes},
            f"Place at {first_gap['position_code']}, the earliest tested concept not demonstrated at the frozen thresholds.",
        )
    last_index = ordered_codes.index(outcomes[-1]["position_code"])
    if last_index + 1 >= len(ordered_codes):
        return PlacementDecision(
            PlacementRecommendation.Decision.CURRICULUM_COMPLETE,
            None,
            [],
            {"concepts": outcomes},
            "All available tested OG+ concepts were demonstrated; specialist completion review is recommended.",
        )
    next_code = ordered_codes[last_index + 1]
    return PlacementDecision(
        PlacementRecommendation.Decision.PLACE,
        next_code,
        [],
        {"concepts": outcomes},
        f"All tested concepts were demonstrated; continue at the next concept, {next_code}.",
    )


@transaction.atomic
def generate_recommendation(evidence: PlacementEvidence) -> PlacementRecommendation:
    evidence.full_clean()
    positions = list(
        evidence.curriculum.positions.filter(is_deleted=False).order_by("sequence_order")
    )
    ordered_codes = [position.code for position in positions]
    if evidence.instrument == PlacementEvidence.Instrument.PFR_PLACEMENT:
        decision = score_pfr_placement(evidence.raw_results, ordered_codes)
    else:
        decision = score_og_placement(evidence.raw_results, ordered_codes, evidence.instrument)
    by_code = {position.code: position for position in positions}
    recommended_position = by_code.get(decision.position_code)

    try:
        advisory = get_instructional_ai_service().placement_narrative(
            {
                "instrument": evidence.instrument,
                "assessment_version": evidence.assessment_version,
                "instructional_grade_band": evidence.instructional_grade_band,
                "deficit_profile": decision.deficit_profile,
                "deterministic_rationale": decision.rationale,
                "narrative_context": (
                    evidence.supporting_context.get("instructional_narrative", "")
                    if getattr(settings, "INSTRUCTIONAL_AI_ALLOW_NARRATIVE", False)
                    else ""
                ),
            }
        )
        ai_metadata = (
            {"provider": advisory.provider, "model": advisory.model, **advisory.metadata}
            if advisory
            else {"provider": "disabled"}
        )
    except InstructionalAIError:
        advisory = None
        ai_metadata = {"provider": "unavailable", "advisory_status": "provider_error"}
    recommendation, _ = PlacementRecommendation.objects.update_or_create(
        evidence=evidence,
        defaults={
            "center": evidence.center,
            "recommended_curriculum": evidence.curriculum,
            "recommended_position": recommended_position,
            "decision": decision.decision,
            "status": PlacementRecommendation.Status.PENDING,
            "deficit_profile": decision.deficit_profile,
            "rule_trace": decision.rule_trace,
            "rationale": decision.rationale,
            "advisory_narrative": advisory.text if advisory else "",
            "ai_metadata": ai_metadata,
            "final_position": None,
            "final_curriculum": None,
            "override_rationale": "",
            "evidence_considered": {},
            "confirmed_by": None,
            "confirmed_at": None,
            "resulting_placement": None,
            "created_by": evidence.created_by,
            "updated_by": evidence.updated_by,
        },
    )
    recommendation.recommended_sequence.all().delete()
    start_index = positions.index(recommended_position) if recommended_position else 0
    for priority, position in enumerate(positions[start_index : start_index + 5], start=1):
        RecommendedSequencePosition.objects.create(
            recommendation=recommendation,
            position=position,
            priority=priority,
            gap_codes=[gap.get("code") for gap in decision.deficit_profile if gap.get("code")],
            rationale=decision.rationale if priority == 1 else "Next position in the frozen prerequisite sequence.",
        )
    return recommendation


def materialize_sequence_plan(
    recommendation: PlacementRecommendation,
    placement: StudentPlacement,
    specialist,
) -> SequencePlan:
    """Create an idempotent working plan from a confirmed recommendation."""

    existing = SequencePlan.objects.filter(created_from_recommendation=recommendation).first()
    if existing:
        return existing

    active_plans = SequencePlan.objects.select_for_update().filter(
        placement=placement,
        status=SequencePlan.Status.ACTIVE,
        is_deleted=False,
    )
    for active_plan in active_plans:
        active_plan.status = SequencePlan.Status.ARCHIVED
        active_plan.updated_by = specialist
        active_plan.save(update_fields=["status", "updated_by", "updated_at"])

    plan = SequencePlan(
        center=placement.center,
        placement=placement,
        status=SequencePlan.Status.ACTIVE,
        created_from_recommendation=recommendation,
        created_by=specialist,
        updated_by=specialist,
    )
    plan.full_clean()
    plan.save()

    selected_position = recommendation.final_position
    ranked_positions = (
        recommendation.recommended_sequence.filter(
            position__curriculum_id=placement.curriculum_id,
            position__sequence_order__gte=selected_position.sequence_order,
        )
        .select_related("position")
        .order_by("priority")
    )
    positions = [selected_position]
    positions.extend(
        ranked.position
        for ranked in ranked_positions
        if ranked.position_id != selected_position.id
    )
    for order, position in enumerate(positions, start=1):
        item = SequencePlanItem(
            plan=plan,
            position=position,
            order=order,
            status=(
                SequencePlanItem.Status.IN_PROGRESS
                if order == 1
                else SequencePlanItem.Status.PENDING
            ),
        )
        item.full_clean()
        item.save()
    return plan


@transaction.atomic
def confirm_recommendation(
    recommendation: PlacementRecommendation,
    specialist,
    final_position=None,
    override_rationale="",
    evidence_considered=None,
    create_sequence_plan=True,
) -> StudentPlacement:
    recommendation = PlacementRecommendation.objects.select_for_update().select_related(
        "evidence__child", "recommended_curriculum"
    ).get(pk=recommendation.pk)
    selected_position = final_position or recommendation.recommended_position
    if selected_position is None:
        raise ValidationError("Select a final sequence position before confirming this recommendation.")
    selected_curriculum = selected_position.curriculum
    if selected_curriculum.center_id != recommendation.center_id:
        raise ValidationError("The final methodology must belong to the same center.")
    is_override = (
        selected_curriculum.pk != recommendation.recommended_curriculum_id
        or selected_position.pk != getattr(recommendation.recommended_position, "pk", None)
    )
    if is_override and not override_rationale.strip():
        raise ValidationError("A specialist rationale is required when overriding a recommendation.")
    placement_rationale = (
        f"{recommendation.rationale} Specialist override: {override_rationale.strip()}"
        if is_override
        else recommendation.rationale
    )

    child = recommendation.evidence.child
    active = StudentPlacement.objects.select_for_update().filter(
        child=child, is_active=True, is_deleted=False
    ).first()
    if active and active.curriculum_id != selected_curriculum.id:
        active.is_active = False
        active.updated_by = specialist
        active.save(update_fields=["is_active", "updated_by", "updated_at"])
        active = None

    if active is None:
        placement = StudentPlacement(
            center=recommendation.center,
            child=child,
            curriculum=selected_curriculum,
            current_position=selected_position,
            methodology_rationale=placement_rationale,
            placement_evidence={"evidence_id": recommendation.evidence_id, "recommendation_id": recommendation.id},
            placed_by=specialist,
            created_by=specialist,
            updated_by=specialist,
        )
        placement.full_clean()
        placement.save()
    else:
        placement = active
        previous_position = placement.current_position
        placement.current_position = selected_position
        placement.methodology_rationale = placement_rationale
        placement.placement_evidence = {
            "evidence_id": recommendation.evidence_id,
            "recommendation_id": recommendation.id,
        }
        placement.placed_by = specialist
        placement.placed_at = timezone.now()
        placement.updated_by = specialist
        placement.full_clean()
        placement.save()
        if previous_position_id := getattr(previous_position, "id", None):
            if previous_position_id != selected_position.id:
                history = StudentPlacementOverride(
                    center=recommendation.center,
                    placement=placement,
                    previous_position=previous_position,
                    new_position=selected_position,
                    rationale=override_rationale or "Accepted a new deterministic placement recommendation.",
                    evidence_considered=evidence_considered or {},
                    source_recommendation=recommendation,
                    specialist=specialist,
                    created_by=specialist,
                    updated_by=specialist,
                )
                history.full_clean()
                history.save()

    recommendation.final_position = selected_position
    recommendation.final_curriculum = selected_curriculum
    recommendation.override_rationale = override_rationale
    recommendation.evidence_considered = evidence_considered or {}
    recommendation.confirmed_by = specialist
    recommendation.confirmed_at = timezone.now()
    recommendation.resulting_placement = placement
    recommendation.status = (
        PlacementRecommendation.Status.OVERRIDDEN
        if is_override
        else PlacementRecommendation.Status.CONFIRMED
    )
    recommendation.updated_by = specialist
    recommendation.full_clean()
    recommendation.save()
    if create_sequence_plan:
        materialize_sequence_plan(recommendation, placement, specialist)
    return placement

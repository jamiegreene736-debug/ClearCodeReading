from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.assessments.models import Assessment, AssessmentResult, ChildAssessmentResponse


CATEGORY_LABELS = {
    "phonemic_awareness": "Phonemic awareness",
    "letter_sound": "Letter sounds",
    "phonics": "Phonics / decoding",
    "advanced_phonics": "Advanced decoding",
    "sight_words": "Sight words",
    "fluency": "Fluency",
    "vocabulary": "Vocabulary",
    "comprehension": "Comprehension",
    "writing_readiness": "Writing readiness",
    "confidence": "Reading confidence",
}

CATEGORY_WEIGHTS = {
    "phonemic_awareness": Decimal("1.00"),
    "letter_sound": Decimal("1.00"),
    "phonics": Decimal("1.25"),
    "advanced_phonics": Decimal("1.15"),
    "sight_words": Decimal("1.00"),
    "fluency": Decimal("1.20"),
    "vocabulary": Decimal("0.90"),
    "comprehension": Decimal("1.20"),
    "writing_readiness": Decimal("0.80"),
    "confidence": Decimal("0.70"),
}


def calculate_reading_survey_results(responses):
    response_list = list(
        responses.select_related("question", "selected_option").prefetch_related("question__question_options")
        if hasattr(responses, "select_related")
        else responses
    )
    category_scores = build_category_scores(response_list)
    overall_score = calculate_overall_score(category_scores)
    reading_age = map_score_to_reading_age(overall_score)
    grade_equivalent = map_reading_age_to_grade(reading_age)
    strengths = build_strengths(category_scores)
    growth_areas = build_growth_areas(category_scores)
    final_message = f"You are reading at an {reading_age:.1f}-year-old level"

    return {
        "category_scores": category_scores,
        "overall_score": overall_score,
        "reading_age": reading_age,
        "grade_equivalent": grade_equivalent,
        "strengths": strengths,
        "growth_areas": growth_areas,
        "teacher_summary": build_teacher_summary(overall_score, reading_age, strengths, growth_areas),
        "final_message": final_message,
        "response_count": len(response_list),
    }


def build_category_scores(responses):
    buckets = defaultdict(lambda: {"earned": Decimal("0"), "possible": Decimal("0"), "responses": 0})

    for response in responses:
        category = response.question.category
        earned = response_score(response)
        possible = response_possible_score(response)
        buckets[category]["earned"] += earned
        buckets[category]["possible"] += possible
        buckets[category]["responses"] += 1

    category_scores = {}
    for category, bucket in buckets.items():
        possible = bucket["possible"] or Decimal("1")
        percent = int(round((bucket["earned"] / possible) * 100))
        category_scores[category] = {
            "label": CATEGORY_LABELS.get(category, category.replace("_", " ").title()),
            "earned": float(bucket["earned"]),
            "possible": float(bucket["possible"]),
            "score": max(0, min(100, percent)),
            "responses": bucket["responses"],
        }
    return category_scores


def response_score(response):
    if response.score_value is not None:
        return Decimal(str(response.score_value))
    selected_option = getattr(response, "selected_option", None)
    if getattr(response, "selected_option_id", None) and selected_option:
        return Decimal(str(selected_option.score_value))
    if response.is_correct is True:
        return Decimal("1")
    return Decimal("0")


def response_possible_score(response):
    question_options = getattr(response.question, "question_options", None)
    if question_options is not None:
        if hasattr(question_options, "filter"):
            options = list(question_options.filter(is_deleted=False))
        elif hasattr(question_options, "all"):
            options = list(question_options.all())
        else:
            options = list(question_options)
        if options:
            max_score = max(Decimal(str(option.score_value)) for option in options)
            return max_score if max_score > 0 else Decimal("1")
    selected_option = getattr(response, "selected_option", None)
    if getattr(response, "selected_option_id", None) and selected_option:
        score = Decimal(str(selected_option.score_value))
        return score if score > 0 else Decimal("1")
    return Decimal("1")


def calculate_overall_score(category_scores):
    if not category_scores:
        return 0

    weighted_score = Decimal("0")
    total_weight = Decimal("0")
    for category, data in category_scores.items():
        weight = CATEGORY_WEIGHTS.get(category, Decimal("1"))
        weighted_score += Decimal(data["score"]) * weight
        total_weight += weight

    if total_weight == 0:
        return 0
    return int(round(weighted_score / total_weight))


def map_score_to_reading_age(overall_score):
    # Lookup-table equivalent:
    # 0-20 => 4.0, 21-35 => 5.0, 36-50 => 6.0, 51-65 => 7.0,
    # 66-80 => 8.0, 81-90 => 9.0, 91-100 => 10.0-11.0.
    bounded = max(0, min(100, overall_score))
    return round(max(4.0, min(11.0, 4.0 + (bounded / 100) * 7.0)), 1)


def map_reading_age_to_grade(reading_age):
    if reading_age < 5:
        return "Pre-K"
    if reading_age < 6:
        return "Kindergarten"
    if reading_age < 7:
        return "Grade 1"
    if reading_age < 8:
        return "Grade 2"
    if reading_age < 9:
        return "Grade 3"
    if reading_age < 10:
        return "Grade 4"
    return "Grade 5+"


def build_strengths(category_scores):
    strengths = [
        data["label"]
        for data in category_scores.values()
        if data["score"] >= 75
    ]
    return strengths or ["Early reading behaviors are emerging"]


def build_growth_areas(category_scores):
    growth_areas = [
        data["label"]
        for data in category_scores.values()
        if data["score"] < 55
    ]
    return growth_areas or ["Continue building fluency and comprehension with just-right texts"]


def build_teacher_summary(overall_score, reading_age, strengths, growth_areas):
    strength_copy = ", ".join(strengths[:3])
    growth_copy = ", ".join(growth_areas[:3])
    return (
        f"Digital survey score: {overall_score}%. Estimated reading age: {reading_age:.1f}. "
        f"Strengths: {strength_copy}. Priority growth areas: {growth_copy}. "
        "Human evaluator review is recommended before final placement."
    )


@transaction.atomic
def compute_and_persist_assessment_result(assessment_id):
    assessment = Assessment.objects.select_for_update().get(id=assessment_id)
    responses = ChildAssessmentResponse.objects.filter(
        assessment=assessment,
        is_deleted=False,
    ).select_related("question", "selected_option")

    result_data = calculate_reading_survey_results(responses)
    assessment_result, _ = AssessmentResult.objects.update_or_create(
        assessment=assessment,
        defaults={
            "final_scores": {
                "overall_score": result_data["overall_score"],
                "response_count": result_data["response_count"],
                "final_message": result_data["final_message"],
            },
            "reading_age": Decimal(str(result_data["reading_age"])),
            "grade_equivalent": result_data["grade_equivalent"],
            "category_breakdown": result_data["category_scores"],
            "strengths": result_data["strengths"],
            "growth_areas": result_data["growth_areas"],
            "teacher_summary": result_data["teacher_summary"],
        },
    )

    assessment.overall_score = result_data["overall_score"]
    assessment.reading_age = Decimal(str(result_data["reading_age"]))
    assessment.survey_completed_at = assessment.survey_completed_at or timezone.now()
    assessment.scoring = {
        **assessment.scoring,
        "reading_survey": result_data,
    }
    assessment.recommendations = result_data["growth_areas"]
    if assessment.status in {Assessment.Status.PENDING, Assessment.Status.IN_PROGRESS}:
        assessment.status = Assessment.Status.HUMAN_REVIEW
    assessment.save(
        update_fields=[
            "overall_score",
            "reading_age",
            "survey_completed_at",
            "scoring",
            "recommendations",
            "status",
            "updated_at",
        ]
    )

    return assessment_result

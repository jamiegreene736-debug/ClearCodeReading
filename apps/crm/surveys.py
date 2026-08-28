import json
import re
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from apps.assessments.reading_survey import QUESTION_SCORES, score_reading_survey
from apps.blog.models import BlogPost
from apps.crm.models import (
    FormSubmission,
    Lead,
    NewsletterSubscription,
    Opportunity,
)
from apps.crm.services import (
    LeadIntake,
    create_partner_triage,
    ensure_family_enrollment_deal,
    record_form_submission,
)


SURVEY_SITUATIONS = {
    "prek_2_struggling": "Child in Pre-K through 2nd grade who struggles with reading",
    "grade_3_5_struggling": "Child in 3rd through 5th grade who struggles with reading",
    "grade_6_8_struggling": "Child in 6th through 8th grade who struggles with reading",
    "multiple_grade_bands": "More than one child in different grade bands",
    "older_than_grade_8": "Child older than 8th grade or interest on behalf of another family",
    "community_supporter": "Educator, specialist, local parent, donor, supporter, or other",
}

PARENT_BRANCH_SITUATIONS = {
    "prek_2_struggling",
    "grade_3_5_struggling",
    "grade_6_8_struggling",
    "multiple_grade_bands",
}

PARENT_AUDIENCE_SITUATIONS = PARENT_BRANCH_SITUATIONS | {"older_than_grade_8"}

SURVEY_SUPPORTS_TRIED = {
    "school_intervention": "School-based reading intervention",
    "general_tutoring": "General private tutoring",
    "specialized_tutor": "Specialized reading tutor",
    "speech_educational_therapy": "Speech-language pathologist or educational therapist",
    "online_program": "Online reading program, app, or game",
    "medical_consultation": "Pediatrician, neurologist, or psychologist consultation",
    "formal_evaluation": "Formal dyslexia or learning evaluation",
    "changed_schooling": "Changed schools or schooling models because of reading",
    "nothing_yet": "Nothing yet; just starting to look for help",
}

SURVEY_SPENDING = {
    "none": "$0, nothing yet",
    "up_to_500": "$1 to $500",
    "501_2000": "$501 to $2,000",
    "2001_5000": "$2,001 to $5,000",
    "5001_10000": "$5,001 to $10,000",
    "over_10000": "More than $10,000",
    "prefer_not_to_say": "Prefer not to say",
}

SURVEY_COMMITMENTS = {
    "weekly_few_months": "One session per week for a few months",
    "one_two_weekly_three_six_months": "One to two sessions per week for three to six months",
    "two_three_weekly_six_twelve_months": "Two to three sessions per week for six to twelve months",
    "two_three_weekly_school_year": "Two to three sessions per week for a full school year or more",
    "specialist_recommendation": "Whatever the specialist recommends",
}

SURVEY_ONE_TO_ONE_BUDGETS = {
    "up_to_75": "Up to $75",
    "up_to_100": "Up to $100",
    "up_to_125": "Up to $125",
    "up_to_150": "Up to $150",
    "up_to_200": "Up to $200",
    "over_200": "More than $200",
    "scholarship_esa": "Only if covered by scholarship or ESA funds",
}

SURVEY_GROUP_BUDGETS = {
    "up_to_50": "Up to $50",
    "up_to_75": "Up to $75",
    "up_to_100": "Up to $100",
    "up_to_125": "Up to $125",
    "over_125": "More than $125",
    "scholarship_esa": "Only if covered by scholarship or ESA funds",
    "prefer_one_to_one": "Prefer 1-on-1 sessions",
}

SURVEY_ENGAGEMENTS = {
    "priority_waitlist": "Join the priority enrollment waitlist",
    "consultation": "Schedule a free specialist consultation",
    "opening_updates": "Receive updates about the center opening",
    "community_partner": "Partner as an advocate, referral source, or donor",
    "refer_family": "Refer another family who needs help",
    "professional_connection": "Connect ClearCode with a school, pediatrician, or evaluator",
    "career_interest": "Educator or specialist interested in working at ClearCode",
    "general_email": "Keep me on the general email list",
}

FAMILY_ONLY_ENGAGEMENTS = {"priority_waitlist", "consultation"}
PARTNER_SIGNAL_ENGAGEMENTS = {
    "community_partner",
    "refer_family",
    "professional_connection",
}

SURVEY_VALUE_LABELS = {
    **SURVEY_SITUATIONS,
    **SURVEY_SUPPORTS_TRIED,
    **SURVEY_SPENDING,
    **SURVEY_COMMITMENTS,
    **SURVEY_ONE_TO_ONE_BUDGETS,
    **SURVEY_GROUP_BUDGETS,
    **SURVEY_ENGAGEMENTS,
}

SURVEY_FIELD_LABELS = {
    "home_zip": "Home ZIP code",
    "respondent_situation": "Situation",
    "supports_tried": "Reading supports tried",
    "annual_reading_spend": "Reading support spend in the last 12 months",
    "commitment_preference": "Realistic family commitment",
    "one_to_one_budget": "Maximum 1-on-1 session budget",
    "small_group_budget": "Maximum small-group session budget",
    "engagement_interests": "How they want to engage",
    "email_consent": "Email consent",
    "survey_placement": "Survey placement",
    "blog_post_title": "Source article",
    "child_name": "Child first name",
    "child_age": "Child age",
    "child_grade": "Child grade",
    "digital_reading_result": "Digital reading result",
    "parent_inventory_result": "Parent reading inventory result",
}


class SurveySubmissionError(ValueError):
    pass


@dataclass(frozen=True)
class SurveySource:
    path: str
    placement: str
    blog_post_title: str = ""
    blog_post_slug: str = ""


@dataclass(frozen=True)
class EarlyInterestSurveyAnswers:
    contact_name: str
    contact_email: str
    home_zip: str
    respondent_situation: str
    supports_tried: list[str]
    annual_reading_spend: str
    commitment_preference: str
    one_to_one_budget: str
    small_group_budget: str
    engagement_interests: list[str]

    @property
    def uses_parent_branch(self) -> bool:
        return self.respondent_situation in PARENT_BRANCH_SITUATIONS

    @property
    def audience(self) -> str:
        if self.respondent_situation in PARENT_AUDIENCE_SITUATIONS:
            return Lead.Audience.PARENT
        return Lead.Audience.OTHER

    @property
    def estimated_students(self) -> int | None:
        if self.respondent_situation == "multiple_grade_bands":
            return 2
        if self.respondent_situation in PARENT_AUDIENCE_SITUATIONS:
            return 1
        return None

    def as_submission_data(self, source: SurveySource) -> dict[str, object]:
        data: dict[str, object] = {
            "name": self.contact_name,
            "email": self.contact_email,
            "email_consent": "yes",
            "home_zip": self.home_zip,
            "respondent_situation": self.respondent_situation,
            "supports_tried": self.supports_tried,
            "annual_reading_spend": self.annual_reading_spend,
            "commitment_preference": self.commitment_preference,
            "one_to_one_budget": self.one_to_one_budget,
            "small_group_budget": self.small_group_budget,
            "engagement_interests": self.engagement_interests,
            "survey_placement": source.placement,
        }
        if source.blog_post_title:
            data.update(
                {
                    "blog_post_title": source.blog_post_title,
                    "blog_post_slug": source.blog_post_slug,
                }
            )
        return data


def _required_choice(post_data, key: str, choices: dict[str, str]) -> str:
    value = str(post_data.get(key, "")).strip()
    if value not in choices:
        raise SurveySubmissionError(f"Choose a valid answer for {key}.")
    return value


def _optional_choice(post_data, key: str, choices: dict[str, str]) -> str:
    value = str(post_data.get(key, "")).strip()
    if value and value not in choices:
        raise SurveySubmissionError(f"Choose a valid answer for {key}.")
    return value


def _multi_choice(post_data, key: str, choices: dict[str, str]) -> list[str]:
    values = post_data.getlist(key) if hasattr(post_data, "getlist") else post_data.get(key, [])
    if isinstance(values, str):
        values = [values]
    normalized = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    if set(normalized) - set(choices):
        raise SurveySubmissionError(f"Choose only valid answers for {key}.")
    return normalized


def parse_early_interest_survey(post_data) -> EarlyInterestSurveyAnswers:
    contact_name = str(post_data.get("name", "")).strip()[:255]
    contact_email = str(post_data.get("email", "")).strip().lower()
    home_zip = str(post_data.get("home_zip", "")).strip()
    if not contact_name:
        raise SurveySubmissionError("Enter your first and last name.")
    try:
        if len(contact_email) > 254:
            raise ValidationError("Email address is too long.")
        validate_email(contact_email)
    except ValidationError as exc:
        raise SurveySubmissionError("Enter a valid email address.") from exc
    if post_data.get("email_consent") != "yes":
        raise SurveySubmissionError("Email consent is required for this survey.")
    if not re.fullmatch(r"\d{5}", home_zip):
        raise SurveySubmissionError("Enter a five-digit ZIP code.")

    situation = _required_choice(post_data, "respondent_situation", SURVEY_SITUATIONS)
    supports_tried = _multi_choice(post_data, "supports_tried", SURVEY_SUPPORTS_TRIED)
    annual_spend = _optional_choice(post_data, "annual_reading_spend", SURVEY_SPENDING)
    commitment = _optional_choice(post_data, "commitment_preference", SURVEY_COMMITMENTS)
    one_to_one_budget = _optional_choice(post_data, "one_to_one_budget", SURVEY_ONE_TO_ONE_BUDGETS)
    small_group_budget = _optional_choice(post_data, "small_group_budget", SURVEY_GROUP_BUDGETS)
    engagements = _multi_choice(post_data, "engagement_interests", SURVEY_ENGAGEMENTS)
    if not engagements:
        raise SurveySubmissionError("Choose at least one way to engage with ClearCode.")
    if "nothing_yet" in supports_tried and len(supports_tried) > 1:
        raise SurveySubmissionError("Nothing yet cannot be combined with other supports.")

    conditional_values_present = any(
        [supports_tried, annual_spend, commitment, one_to_one_budget, small_group_budget]
    )
    if situation not in PARENT_BRANCH_SITUATIONS and conditional_values_present:
        raise SurveySubmissionError("Questions 5 through 9 do not apply to this response.")
    if situation not in PARENT_BRANCH_SITUATIONS and set(engagements) & FAMILY_ONLY_ENGAGEMENTS:
        raise SurveySubmissionError("Family enrollment choices do not apply to this response.")

    return EarlyInterestSurveyAnswers(
        contact_name=contact_name,
        contact_email=contact_email,
        home_zip=home_zip,
        respondent_situation=situation,
        supports_tried=supports_tried,
        annual_reading_spend=annual_spend,
        commitment_preference=commitment,
        one_to_one_budget=one_to_one_budget,
        small_group_budget=small_group_budget,
        engagement_interests=engagements,
    )


def resolve_survey_source(post_data) -> SurveySource:
    source_path = str(post_data.get("source_path", "")).strip()
    if source_path == "/survey/":
        return SurveySource(path=source_path, placement="Main survey page")

    blog_slug = str(post_data.get("blog_post_slug", "")).strip()
    blog_post = BlogPost.objects.published().filter(slug=blog_slug).first()
    if blog_post and source_path == blog_post.get_absolute_url():
        return SurveySource(
            path=source_path,
            placement="Blog article",
            blog_post_title=blog_post.title,
            blog_post_slug=blog_post.slug,
        )
    raise SurveySubmissionError("The survey source is not valid.")


def _grade_band_for_situation(situation: str) -> str:
    return {
        "prek_2_struggling": Opportunity.GradeBand.PREK_2,
        "grade_3_5_struggling": Opportunity.GradeBand.GRADE_3_5,
        "grade_6_8_struggling": Opportunity.GradeBand.GRADE_6_8,
        "multiple_grade_bands": Opportunity.GradeBand.MULTI_CHILD,
    }.get(situation, "")


@transaction.atomic
def record_early_interest_survey(*, answers: EarlyInterestSurveyAnswers, source: SurveySource):
    has_partner_signal = bool(set(answers.engagement_interests) & PARTNER_SIGNAL_ENGAGEMENTS)
    notes = (
        f"Early interest survey from {source.placement}. "
        f"Situation: {SURVEY_SITUATIONS[answers.respondent_situation]}. "
        f"Engagement: {', '.join(SURVEY_ENGAGEMENTS[item] for item in answers.engagement_interests)}."
    )
    submission_data = answers.as_submission_data(source)
    lead, submission = record_form_submission(
        intake=LeadIntake(
            contact_email=answers.contact_email,
            contact_name=answers.contact_name,
            school_name="Family interest survey" if answers.audience == Lead.Audience.PARENT else "Community interest survey",
            audience=answers.audience,
            estimated_students=answers.estimated_students,
            notes=notes,
            metadata={
                "home_zip": answers.home_zip,
                "respondent_situation": answers.respondent_situation,
                "engagement_interests": answers.engagement_interests,
                "latest_interest_survey": submission_data,
                "partner_interest": has_partner_signal,
            },
        ),
        form_type=FormSubmission.FormType.SURVEY,
        source_path=source.path,
        submitted_data=submission_data,
    )

    if answers.uses_parent_branch:
        deal, _created = ensure_family_enrollment_deal(lead=lead)
        deal.grade_band = _grade_band_for_situation(answers.respondent_situation)
        deal.in_catchment_zip = answers.home_zip
        if (
            answers.one_to_one_budget == "scholarship_esa"
            or answers.small_group_budget == "scholarship_esa"
        ):
            deal.funding_type = Opportunity.FundingType.ESA
        if (
            "priority_waitlist" in answers.engagement_interests
            and deal.stage == Opportunity.Stage.FAMILY_LEAD_NURTURE
        ):
            deal.stage = Opportunity.Stage.FAMILY_WAITLIST
        deal.metadata = {
            **(deal.metadata or {}),
            "latest_interest_survey_id": submission.pk,
            "engagement_interests": answers.engagement_interests,
            "commitment_preference": answers.commitment_preference,
            "one_to_one_budget": answers.one_to_one_budget,
            "small_group_budget": answers.small_group_budget,
        }
        deal.save()

    if has_partner_signal:
        create_partner_triage(lead=lead, submission=submission)

    existing_name = (
        NewsletterSubscription.objects.filter(email=answers.contact_email)
        .values_list("name", flat=True)
        .first()
    )
    NewsletterSubscription.objects.update_or_create(
        email=answers.contact_email,
        defaults={
            "name": answers.contact_name or existing_name or "",
            "status": NewsletterSubscription.Status.ACTIVE,
            "consented_at": timezone.now(),
            "unsubscribed_at": None,
            "source_path": source.path,
            "consent_version": "early-interest-v1",
        },
    )
    return lead, submission


def structured_assessment_submission(post_data) -> dict[str, object]:
    child_name = str(post_data.get("child_name", "")).strip()[:255]
    try:
        child_age = int(post_data.get("child_age", ""))
    except (TypeError, ValueError):
        child_age = 0
    if child_age not in range(4, 19):
        child_age = 0

    try:
        answer_indexes = json.loads(post_data.get("assessment_answers", "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        answer_indexes = {}
    if not isinstance(answer_indexes, dict):
        answer_indexes = {}
    answer_indexes = {
        key: value
        for key, value in answer_indexes.items()
        if key in QUESTION_SCORES
        and isinstance(value, int)
        and 0 <= value < len(QUESTION_SCORES[key])
    }
    digital_result = score_reading_survey(answer_indexes, child_age or None)

    child_grade = str(post_data.get("child_grade", "")).strip()
    grade_prefix = {
        "kindergarten": "kindergarten-",
        "grade_1": "first-grade-",
        "grade_2": "second-grade-",
        "grade_3": "third-plus-",
        "grade_4": "third-plus-",
        "grade_5": "third-plus-",
        "grade_6": "third-plus-",
        "grade_7": "third-plus-",
        "grade_8": "third-plus-",
        "high_school": "third-plus-",
    }.get(child_grade, "")
    try:
        inventory_answers = json.loads(post_data.get("inventory_answers", "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        inventory_answers = {}
    if not isinstance(inventory_answers, dict):
        inventory_answers = {}
    inventory_answers = {
        key: value
        for key, value in inventory_answers.items()
        if grade_prefix and key.startswith(grade_prefix) and isinstance(value, bool)
    }
    inventory_total = {
        "kindergarten": 20,
        "grade_1": 25,
        "grade_2": 25,
    }.get(child_grade, 24 if grade_prefix else 0)
    inventory_resource_at = {
        "kindergarten": 13,
        "grade_1": 16,
        "grade_2": 19,
    }.get(child_grade, 19 if grade_prefix else 0)
    yes_count = sum(value is True for value in inventory_answers.values())
    try:
        stopped_group = int(post_data.get("inventory_stopped_group", ""))
    except (TypeError, ValueError):
        stopped_group = None

    home_zip = str(post_data.get("home_zip", "")).strip()
    return {
        "child_name": child_name,
        "child_age": child_age or None,
        "home_zip": home_zip if re.fullmatch(r"\d{5}(?:-\d{4})?", home_zip) else "",
        "child_grade": child_grade if grade_prefix else "",
        "digital_reading_result": digital_result,
        "parent_inventory_result": {
            "answers": inventory_answers,
            "answered_count": len(inventory_answers),
            "yes_count": yes_count,
            "total_questions": inventory_total,
            "support_recommended": stopped_group is not None or yes_count < inventory_resource_at,
            "stopped_group_index": stopped_group,
        },
    }


def apply_structured_assessment_to_deal(*, lead: Lead, assessment_data: dict[str, object]):
    deal, _created = ensure_family_enrollment_deal(lead=lead)
    child_name = str(assessment_data.get("child_name") or "").strip()
    child_grade = str(assessment_data.get("child_grade") or "").strip()
    deal.student_name = child_name or deal.student_name
    deal.in_catchment_zip = str(assessment_data.get("home_zip") or "") or deal.in_catchment_zip
    deal.grade_band = {
        "kindergarten": Opportunity.GradeBand.PREK_2,
        "grade_1": Opportunity.GradeBand.PREK_2,
        "grade_2": Opportunity.GradeBand.PREK_2,
        "grade_3": Opportunity.GradeBand.GRADE_3_5,
        "grade_4": Opportunity.GradeBand.GRADE_3_5,
        "grade_5": Opportunity.GradeBand.GRADE_3_5,
        "grade_6": Opportunity.GradeBand.GRADE_6_8,
        "grade_7": Opportunity.GradeBand.GRADE_6_8,
        "grade_8": Opportunity.GradeBand.GRADE_6_8,
    }.get(child_grade, deal.grade_band)
    deal.metadata = {
        **(deal.metadata or {}),
        "latest_reading_assessment": assessment_data,
    }
    deal.save()
    return deal

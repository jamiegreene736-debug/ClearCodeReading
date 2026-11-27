from urllib.parse import urlsplit

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.api.permissions import has_coppa_consent, has_school_membership
from apps.schools.models import SchoolMembership
from apps.progress.models import OgFamilyPlan, OgWeeklyAssessment
from apps.users.models import CustomUser


FRACTIONS = {
    "word_spelling": "Words spelled correctly",
    "red_words": "Red Words spelled correctly",
    "capitalization": "Correct capitalization",
    "punctuation": "Correct punctuation",
    "sentence_spelling": "Sentence words spelled correctly",
    "comprehension": "Comprehension questions answered correctly",
}


class WeeklyAssessmentForm(forms.Form):
    assessed_on = forms.DateField(label="Date of assessment", widget=forms.DateInput(attrs={"type": "date"}))
    concept = forms.TypedChoiceField(coerce=int)
    word_spelling = forms.CharField(label=FRACTIONS["word_spelling"], max_length=15)
    red_words = forms.CharField(label=FRACTIONS["red_words"], max_length=15)
    word_notes = forms.CharField(label="Word dictation notes", required=False, max_length=2000, widget=forms.Textarea)
    capitalization = forms.CharField(label=FRACTIONS["capitalization"], max_length=15)
    spacing = forms.ChoiceField(label="Appropriate word spacing?", choices=[("", "Choose"), ("yes", "Yes"), ("no", "No")])
    punctuation = forms.CharField(label=FRACTIONS["punctuation"], max_length=15)
    sentence_spelling = forms.CharField(label=FRACTIONS["sentence_spelling"], max_length=15)
    sentence_notes = forms.CharField(label="Sentence dictation notes", required=False, max_length=2000, widget=forms.Textarea)
    fluency_title = forms.CharField(label="Oral reading fluency passage title", max_length=255)
    wcpm = forms.IntegerField(label="Correct words read in one minute", min_value=0, max_value=1000)
    fluency_total = forms.IntegerField(label="Total words attempted in one minute", required=False, min_value=1, max_value=1000)
    fluency_notes = forms.CharField(label="Oral reading fluency notes", required=False, max_length=2000, widget=forms.Textarea)
    comprehension_title = forms.CharField(label="Comprehension passage title", max_length=255)
    comprehension = forms.CharField(label=FRACTIONS["comprehension"], max_length=15)
    comprehension_notes = forms.CharField(label="Comprehension notes", required=False, max_length=2000, widget=forms.Textarea)
    concept_passed = forms.BooleanField(label="Reader passed this concept (unlocks family resources)", required=False)

    def __init__(self, *args, concepts, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["concept"].choices = [(n, f"Concept {n}") for n in concepts]
        for name in FRACTIONS:
            self.fields[name].help_text = "Enter correct / attempted, for example 4/5."
        for field in self.fields.values():
            field.widget.attrs["class"] = "mt-1 block max-w-full rounded-lg border border-ink/20 px-3 py-2"

    def clean(self):
        data = super().clean()
        if data.get("assessed_on") and data["assessed_on"] > timezone.localdate():
            self.add_error("assessed_on", "Choose today or an earlier date.")
        for name in FRACTIONS:
            if name not in data:
                continue
            parts = [part.strip() for part in data[name].split("/")]
            if len(parts) != 2 or not all(part.isascii() and part.isdigit() for part in parts):
                self.add_error(name, "Enter a fraction such as 4/5.")
                continue
            correct, attempted = map(int, parts)
            if not (0 <= correct <= attempted <= 1000 and attempted > 0):
                self.add_error(name, "Correct must be between zero and the number attempted.")
            else:
                data[name] = {"correct": correct, "attempted": attempted}
        if data.get("fluency_total") is not None and data.get("wcpm") is not None:
            if data["wcpm"] > data["fluency_total"]:
                self.add_error("fluency_total", "Total attempted cannot be less than correct words.")
            else:
                data["fluency_accuracy"] = round(100 * data["wcpm"] / data["fluency_total"], 1)
        return data


def can_record_og(user, child):
    if not child.school_id or not has_coppa_consent(child):
        return False
    if user.is_superuser or user.role == CustomUser.Role.SUPER_ADMIN:
        return True
    if user.role == CustomUser.Role.SCHOOL_ADMIN:
        return has_school_membership(user, child.school, {SchoolMembership.Role.OWNER, SchoolMembership.Role.ADMIN})
    return (
        user.role == CustomUser.Role.TEACHER
        and str((child.learning_profile or {}).get("assigned_teacher_id")) == str(user.pk)
        and has_school_membership(user, child.school, {SchoolMembership.Role.TEACHER, SchoolMembership.Role.SPECIALIST})
    )


def _drive_resource(url):
    """Do not expose a bad or unrelated link saved through a bulk/admin path."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme == "https" and parts.netloc == "drive.google.com" and parts.path.startswith("/file/d/"):
        return url
    return None


def family_progress(child):
    plan = OgFamilyPlan.objects.filter(child=child).first()
    if not plan:
        return None
    reports = list(plan.assessments.all())
    passed = {r.concept for r in reports if r.concept_passed}
    concepts = plan.concepts()
    current = next((n for n in concepts if n not in passed), None)
    packs = []
    for pack in plan.resource_packs.filter(concept__in=concepts).order_by("concept"):
        unlocked = pack.concept in passed
        links = []
        if unlocked:
            for label, field in (
                ("Family Connection Letter", pack.connection_letter),
                ("Fluency Practice", pack.fluency_practice),
                ("Decodable Passage", pack.decodable_passage),
            ):
                if url := _drive_resource(field):
                    links.append({"label": label, "url": url})
        packs.append({"concept": pack.concept, "unlocked": unlocked, "links": links})
    latest = next((report for report in reports if report.concept in concepts), None)
    summary = None
    if latest:
        summary = {
            "date": latest.assessed_on,
            "concept": latest.concept,
            "fractions": [{"label": label, **latest.scores[name]} for name, label in FRACTIONS.items()],
            "spacing": latest.scores["spacing"],
            "fluency_title": latest.scores["fluency_title"],
            "comprehension_title": latest.scores["comprehension_title"],
            "wcpm": latest.scores["wcpm"],
            "accuracy": latest.scores.get("fluency_accuracy"),
        }
    return {"current_concept": current, "progress_report": summary, "resource_packs": packs}


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def weekly_assessment(request, child_id):
    plan = get_object_or_404(OgFamilyPlan.objects.select_related("child"), child_id=child_id, child__is_deleted=False)
    if not can_record_og(request.user, plan.child):
        raise PermissionDenied
    state = family_progress(plan.child)
    initial = {"assessed_on": timezone.localdate(), "concept": state["current_concept"]}
    form = WeeklyAssessmentForm(
        request.POST if request.method == "POST" else None, initial=initial, concepts=plan.concepts()
    )
    if request.method == "POST" and form.is_valid():
        scores = form.cleaned_data.copy()
        with transaction.atomic():
            locked = OgFamilyPlan.objects.select_for_update().get(pk=plan.pk)
            concept = scores.pop("concept")
            assessed_on = scores.pop("assessed_on")
            passed = scores.pop("concept_passed")
            mastered = set(locked.assessments.filter(concept_passed=True).values_list("concept", flat=True))
            current = next((n for n in locked.concepts() if n not in mastered), None)
            if concept not in locked.concepts():
                form.add_error("concept", "This concept is no longer on the reader’s plan.")
            elif passed and concept != current:
                form.add_error("concept", "Only the current concept can be marked as passed.")
            else:
                _, created = OgWeeklyAssessment.objects.get_or_create(
                    plan=locked, concept=concept, assessed_on=assessed_on,
                    defaults={"teacher": request.user, "scores": scores, "concept_passed": passed},
                )
                if created:
                    messages.success(request, "Weekly OG+ assessment saved.")
                    return redirect("og_weekly_assessment", child_id=child_id)
                form.add_error(None, "An assessment for this reader, date, and concept already exists.")
    return render(request, "portal/og_weekly_assessment.html", {
        "form": form, "child": plan.child, "state": state, "recent_reports": plan.assessments.all()[:10],
    })

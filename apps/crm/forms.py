from django import forms
from django.db.models import Q

from apps.crm.models import Company, Lead, Opportunity
from apps.users.models import CustomUser


class ContactForm(forms.ModelForm):
    company_name = forms.CharField(
        max_length=255,
        required=False,
        label="Or create a company",
        help_text="Leave this blank when selecting an existing company.",
    )

    class Meta:
        model = Lead
        fields = [
            "contact_name",
            "contact_email",
            "contact_phone",
            "audience",
            "organization_name",
            "company",
            "source",
            "status",
            "assigned_to",
            "estimated_students",
            "notes",
        ]
        widgets = {
            "contact_email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "contact_phone": forms.TextInput(attrs={"autocomplete": "tel"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].queryset = Company.objects.filter(is_deleted=False).order_by("name")
        self.fields["assigned_to"].queryset = CustomUser.objects.filter(
            is_active=True,
            is_deleted=False,
        ).filter(
            Q(is_superuser=True) | Q(is_staff=True) | Q(role=CustomUser.Role.SUPER_ADMIN)
        ).distinct().order_by("first_name", "last_name", "email")
        self.fields["company"].required = False
        self.fields["assigned_to"].required = False

    def clean_contact_email(self):
        return self.cleaned_data["contact_email"].strip().lower()

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("company") and cleaned_data.get("company_name", "").strip():
            self.add_error("company_name", "Choose an existing company or create a new one, not both.")
        return cleaned_data


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["name", "website", "owner", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = CustomUser.objects.filter(
            is_active=True,
            is_deleted=False,
        ).filter(
            Q(is_superuser=True) | Q(is_staff=True) | Q(role=CustomUser.Role.SUPER_ADMIN)
        ).distinct().order_by("first_name", "last_name", "email")
        self.fields["owner"].required = False

    def clean_name(self):
        return self.cleaned_data["name"].strip()


class DealForm(forms.ModelForm):
    class Meta:
        model = Opportunity
        fields = [
            "lead",
            "company",
            "pipeline",
            "stage",
            "owner",
            "priority",
            "student_name",
            "term_year",
            "campaign_year",
            "program_name",
            "cycle_year",
            "investment_round",
            "funding_type",
            "esa_program",
            "grade_band",
            "in_catchment_zip",
            "referral_source",
            "referral_partner",
            "partner_type",
            "donor_type",
            "gift_level",
            "grant_cycle_application_date",
            "capital_lane",
            "bucket",
            "segment_tags",
            "value",
            "expected_close_date",
            "next_steps",
            "related_deals",
        ]
        widgets = {
            "grant_cycle_application_date": forms.DateInput(attrs={"type": "date"}),
            "expected_close_date": forms.DateInput(attrs={"type": "date"}),
            "next_steps": forms.Textarea(attrs={"rows": 3}),
            "related_deals": forms.SelectMultiple(attrs={"size": 4}),
        }

    def __init__(self, *args, pipeline=None, **kwargs):
        super().__init__(*args, **kwargs)
        selected_pipeline = (
            self.data.get("pipeline")
            or pipeline
            or (self.instance.pipeline if self.instance.pk else Opportunity.Pipeline.FAMILY_ENROLLMENT)
        )
        if selected_pipeline not in Opportunity.Pipeline.values:
            selected_pipeline = Opportunity.Pipeline.FAMILY_ENROLLMENT
        self.fields["stage"].choices = Opportunity.stage_choices_for_pipeline(selected_pipeline)
        if not self.is_bound:
            self.initial.setdefault("pipeline", selected_pipeline)
            self.initial.setdefault("stage", Opportunity.initial_stage_for_pipeline(selected_pipeline))
            if selected_pipeline == Opportunity.Pipeline.FOUNDATION_GRANTS:
                self.initial.setdefault("capital_lane", Opportunity.CapitalLane.FOUNDATION)
        if self.instance.pk:
            self.fields["pipeline"].disabled = True
        if self.is_bound and self.instance.pk and self.instance.needs_naming_review:
            self.instance.metadata = {**self.instance.metadata}
            self.instance.metadata.pop("needs_naming_review", None)

        self.fields["lead"].queryset = self.fields["lead"].queryset.filter(
            is_deleted=False
        ).order_by("contact_name", "organization_name")
        company_queryset = Company.objects.filter(is_deleted=False).order_by("name")
        self.fields["company"].queryset = company_queryset
        self.fields["referral_partner"].queryset = company_queryset
        self.fields["owner"].queryset = CustomUser.objects.filter(
            is_active=True,
            is_deleted=False,
        ).filter(
            Q(is_superuser=True) | Q(is_staff=True) | Q(role=CustomUser.Role.SUPER_ADMIN)
        ).distinct().order_by("first_name", "last_name", "email")
        related = Opportunity.objects.filter(is_deleted=False).order_by("pipeline", "name")
        if self.instance.pk:
            related = related.exclude(pk=self.instance.pk)
        self.fields["related_deals"].queryset = related

    def clean(self):
        cleaned_data = super().clean()
        pipeline = cleaned_data.get("pipeline") or self.instance.pipeline
        stage = cleaned_data.get("stage")
        if stage and stage not in Opportunity.stage_values_for_pipeline(pipeline):
            self.add_error("stage", "Choose a stage from the selected pipeline.")
        capital_lane = cleaned_data.get("capital_lane")
        if pipeline == Opportunity.Pipeline.FOUNDATION_GRANTS:
            cleaned_data["capital_lane"] = Opportunity.CapitalLane.FOUNDATION
        elif pipeline == Opportunity.Pipeline.EQUITY_INVESTMENT and capital_lane == Opportunity.CapitalLane.FOUNDATION:
            self.add_error("capital_lane", "Use ClearCode, Inc. or Both for an investment deal.")
        return cleaned_data

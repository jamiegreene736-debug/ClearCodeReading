from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.crypto import get_random_string

from apps.crm.access import crm_owner_queryset
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
        self.fields["assigned_to"].queryset = crm_owner_queryset()
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
        self.fields["owner"].queryset = crm_owner_queryset()
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
        self.fields["owner"].queryset = crm_owner_queryset()
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


class CrmTeamMemberForm(forms.Form):
    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, required=False, label="Last name")
    email = forms.EmailField(max_length=254, label="Work email")
    password1 = forms.CharField(
        required=False,
        label="Temporary password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Leave both password fields blank to generate a secure temporary password.",
    )
    password2 = forms.CharField(
        required=False,
        label="Confirm temporary password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1", "")
        password2 = cleaned_data.get("password2", "")
        if bool(password1) != bool(password2) or (password1 and password1 != password2):
            self.add_error("password2", "The two password fields must match.")
            return cleaned_data
        if password1:
            try:
                password_validation.validate_password(password1, self._candidate_user(cleaned_data))
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned_data

    def save(self, *, created_by):
        password = self.cleaned_data["password1"] or self._temporary_password()
        candidate = self._candidate_user(self.cleaned_data)
        if not self.cleaned_data["password1"]:
            password_validation.validate_password(password, candidate)
        return CustomUser.objects.create_user(
            username=self._unique_username(self.cleaned_data["email"]),
            email=self.cleaned_data["email"],
            password=password,
            first_name=self.cleaned_data["first_name"].strip(),
            last_name=self.cleaned_data["last_name"].strip(),
            role=CustomUser.Role.CRM_USER,
            is_active=True,
            is_staff=False,
            is_superuser=False,
            metadata={
                "created_from_crm": True,
                "created_by_admin_id": created_by.pk,
                "created_at": timezone.now().isoformat(),
            },
        ), password

    @staticmethod
    def _candidate_user(cleaned_data):
        email = cleaned_data.get("email", "")
        return CustomUser(
            username=email.split("@", 1)[0],
            email=email,
            first_name=cleaned_data.get("first_name", ""),
            last_name=cleaned_data.get("last_name", ""),
            role=CustomUser.Role.CRM_USER,
        )

    @staticmethod
    def _temporary_password():
        return f"ClearCode-{get_random_string(16)}!"

    @staticmethod
    def _unique_username(email):
        base = email.split("@", 1)[0].replace("+", "-")[:120] or "crm-user"
        username = base
        counter = 1
        while CustomUser.objects.filter(username=username).exists():
            counter += 1
            username = f"{base}-{counter}"
        return username

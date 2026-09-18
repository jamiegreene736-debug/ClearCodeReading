import json

from django import forms
from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone

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


class ContactDealSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value:
            option["attrs"]["data-deals"] = json.dumps([
                {
                    "name": deal.name,
                    "pipeline": deal.get_pipeline_display(),
                    "stage": deal.get_stage_display(),
                    "url": reverse("crm_deal_detail", kwargs={"pk": deal.pk}),
                }
                for deal in value.instance.contact_deals
            ])
        return option


class ContactDealChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, contact: Lead) -> str:
        deals = contact.contact_deals
        if not deals:
            return f"{contact} — No existing deals"
        context = "; ".join(
            f"{deal.name} · {deal.get_pipeline_display()} · {deal.get_stage_display()}"
            for deal in deals
        )
        return f"{contact} — {context}"


class DealForm(forms.ModelForm):
    lead = ContactDealChoiceField(
        queryset=Lead.objects.none(), required=False, label="Contact",
        empty_label="Choose a contact…", widget=ContactDealSelect,
    )
    company_name = forms.CharField(
        max_length=255,
        required=False,
        label="Or create a company",
        help_text="Leave this blank when selecting an existing company.",
    )

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

    def __init__(self, *args, pipeline=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self._created_company = None
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
        if (
            self.is_bound and self.instance.pk and self.instance.needs_naming_review
            and self.instance.pipeline != Opportunity.Pipeline.FAMILY_ENROLLMENT
        ):
            self.instance.metadata = {**self.instance.metadata}
            self.instance.metadata.pop("needs_naming_review", None)

        self.fields["lead"].queryset = Lead.objects.filter(
            is_deleted=False
        ).order_by("contact_name", "organization_name").prefetch_related(
            Prefetch(
                "opportunities",
                queryset=Opportunity.objects.filter(is_deleted=False).order_by("pipeline", "name", "pk"),
                to_attr="contact_deals",
            )
        )
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
        if cleaned_data.get("company") and cleaned_data.get("company_name", "").strip():
            self.add_error("company_name", "Choose an existing company or create a new one, not both.")
        pipeline = cleaned_data.get("pipeline") or self.instance.pipeline
        stage = cleaned_data.get("stage")
        if stage and stage not in Opportunity.stage_values_for_pipeline(pipeline):
            self.add_error("stage", "Choose a stage from the selected pipeline.")
        if (
            self.instance.pk
            and self.instance.needs_naming_review
            and pipeline == Opportunity.Pipeline.FAMILY_ENROLLMENT
            and cleaned_data.get("student_name")
            and cleaned_data.get("term_year")
        ):
            self.instance.metadata = {**self.instance.metadata}
            self.instance.metadata.pop("needs_naming_review", None)
        capital_lane = cleaned_data.get("capital_lane")
        if pipeline == Opportunity.Pipeline.FOUNDATION_GRANTS:
            cleaned_data["capital_lane"] = Opportunity.CapitalLane.FOUNDATION
        elif pipeline == Opportunity.Pipeline.EQUITY_INVESTMENT and capital_lane == Opportunity.CapitalLane.FOUNDATION:
            self.add_error("capital_lane", "Use ClearCode, Inc. or Both for an investment deal.")
        return cleaned_data

    def _post_clean(self):
        # The deal's own validation reads company, so resolve a typed name before it runs.
        company_name = (self.cleaned_data.get("company_name") or "").strip()
        if company_name and not self.cleaned_data.get("company"):
            company = Company.objects.filter(name__iexact=company_name, is_deleted=False).first()
            if company is None:
                company = Company.objects.create(name=company_name, owner=self.user)
                self._created_company = company
            self.cleaned_data["company"] = company
        super()._post_clean()
        if self._created_company is not None and self.errors:
            # The deal never saved, so the company it would have belonged to should not linger.
            Company.objects.filter(pk=self._created_company.pk).delete()
            self._created_company = None


class CrmTeamMemberForm(forms.Form):
    hiring_enabled = forms.BooleanField(required=False, label="Teacher hiring access")
    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, required=False, label="Last name")
    email = forms.EmailField(max_length=254, label="Work email")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def save(self, *, created_by):
        return CustomUser.objects.create_user(
            username=self._unique_username(self.cleaned_data["email"]),
            email=self.cleaned_data["email"],
            password=None,
            first_name=self.cleaned_data["first_name"].strip(),
            last_name=self.cleaned_data["last_name"].strip(),
            role=CustomUser.Role.CRM_USER,
            is_active=True,
            is_staff=False,
            is_superuser=False,
            hiring_enabled=self.cleaned_data.get("hiring_enabled", False),
            metadata={
                "created_from_crm": True,
                "created_by_admin_id": created_by.pk,
                "created_at": timezone.now().isoformat(),
            },
        )

    @staticmethod
    def _unique_username(email):
        base = email.split("@", 1)[0].replace("+", "-")[:120] or "crm-user"
        username = base
        counter = 1
        while CustomUser.objects.filter(username=username).exists():
            counter += 1
            username = f"{base}-{counter}"
        return username


class EnrollmentPersonForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = ["contact_name", "contact_email", "contact_phone"]

    def clean_contact_email(self) -> str:
        email = self.cleaned_data["contact_email"].strip().lower()
        if Lead.objects.filter(
            contact_email__iexact=email, is_deleted=False
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another contact already uses this email address.")
        return email

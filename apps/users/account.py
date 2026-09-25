"""Self-service account profile for people who sign in to ClearCode."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views import View

from apps.crm.models import Lead
from apps.users.models import AuditLog, CustomUser, Profile
from apps.users.portal_views import PortalAuthMixin


COMMON_TIMEZONES = (
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Phoenix",
    "America/Los_Angeles",
    "America/Anchorage",
    "Pacific/Honolulu",
)

ROLE_INTRO = {
    CustomUser.Role.GUARDIAN: (
        "Keep this current so we can schedule sessions and reach you about your child."
    ),
    CustomUser.Role.TEACHER: (
        "Keep this current so families and the ClearCode team know how to reach you."
    ),
    CustomUser.Role.STUDENT: "Update the name and time zone we use for your learning.",
}


class AccountProfileForm(forms.Form):
    display_name = forms.CharField(
        max_length=255,
        required=False,
        label="What should we call you?",
    )
    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, required=False, label="Last name")
    phone_number = forms.CharField(max_length=32, required=False, label="Phone")
    preferred_contact_method = forms.ChoiceField(
        required=False,
        label="Best way to reach you",
        choices=[("", "No preference")] + list(Profile.ContactMethod.choices),
    )
    timezone = forms.ChoiceField(label="Time zone")
    organization_name = forms.CharField(
        max_length=255,
        required=False,
        label="Organization, school, or firm",
    )
    job_title = forms.CharField(max_length=120, required=False, label="Title")
    city = forms.CharField(max_length=120, required=False, label="City")
    region = forms.CharField(max_length=80, required=False, label="State")
    postal_code = forms.CharField(max_length=20, required=False, label="Postal code")
    about = forms.CharField(
        required=False,
        label="Note for our team",
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": 500}),
    )

    def __init__(self, *args, user: CustomUser, **kwargs):
        super().__init__(*args, **kwargs)
        current = self.initial.get("timezone") or "America/New_York"
        zones = list(COMMON_TIMEZONES)
        if current not in zones:
            zones.insert(0, current)
        self.fields["timezone"].choices = [(zone, zone.replace("_", " ")) for zone in zones]
        if user.role == CustomUser.Role.TEACHER:
            self.fields["organization_name"].label = "School or organization"
            self.fields["job_title"].label = "Role at your school"
        elif user.role == CustomUser.Role.GUARDIAN:
            self.fields["organization_name"].label = "School, if you want us to know it"
            self.fields["job_title"].label = "How you describe yourself"

    def clean_timezone(self):
        value = self.cleaned_data["timezone"]
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise forms.ValidationError("Choose a time zone from the list.") from exc
        return value

    def clean_about(self):
        return (self.cleaned_data.get("about") or "").strip()[:500]

    def clean_phone_number(self):
        return " ".join((self.cleaned_data.get("phone_number") or "").split())


def profile_initial(user: CustomUser, profile: Profile) -> dict:
    return {
        "display_name": profile.display_name,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone_number": user.phone_number,
        "preferred_contact_method": profile.preferred_contact_method,
        "timezone": profile.timezone,
        "organization_name": profile.organization_name,
        "job_title": profile.job_title,
        "city": profile.city,
        "region": profile.region,
        "postal_code": profile.postal_code,
        "about": profile.about,
    }


def sync_linked_contacts(user: CustomUser, profile: Profile) -> None:
    """Refresh the CRM contact card when the person updates their own details."""
    full_name = user.get_full_name().strip()
    leads = Lead.objects.filter(is_deleted=False).filter(
        Q(linked_user=user) | Q(contact_email__iexact=user.email, linked_user__isnull=True)
    )
    for lead in leads:
        lead.contact_phone = user.phone_number
        if full_name:
            lead.contact_name = full_name
        if profile.organization_name:
            lead.organization_name = profile.organization_name
        if lead.linked_user_id is None:
            lead.linked_user = user
        lead.save()


class AccountProfileView(PortalAuthMixin, View):
    template_name = "portal/account.html"

    def get(self, request):
        user = request.user
        profile, _created = Profile.objects.get_or_create(user=user)
        return self.render(request, profile, AccountProfileForm(user=user, initial=profile_initial(user, profile)))

    def post(self, request):
        user = request.user
        profile, _created = Profile.objects.get_or_create(user=user)
        if request.POST.get("save") == "password":
            password_form = PasswordChangeForm(user, request.POST)
            profile_form = AccountProfileForm(user=user, initial=profile_initial(user, profile))
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, password_form.user)
                AuditLog.objects.create(
                    actor=user,
                    action="account.password_changed",
                    entity_type="CustomUser",
                    entity_id=str(user.pk),
                )
                messages.success(request, "Your password is updated.")
                return redirect("account_profile")
            return self.render(request, profile, profile_form, password_form)

        profile_form = AccountProfileForm(request.POST, user=user)
        if not profile_form.is_valid():
            return self.render(request, profile, profile_form)

        data = profile_form.cleaned_data
        user.first_name = data["first_name"].strip()
        user.last_name = data["last_name"].strip()
        user.phone_number = data["phone_number"]
        user.save(update_fields=["first_name", "last_name", "phone_number", "updated_at"])
        profile.display_name = data["display_name"].strip()
        profile.preferred_contact_method = data["preferred_contact_method"]
        profile.timezone = data["timezone"]
        profile.organization_name = data["organization_name"].strip()
        profile.job_title = data["job_title"].strip()
        profile.city = data["city"].strip()
        profile.region = data["region"].strip()
        profile.postal_code = data["postal_code"].strip()
        profile.about = data["about"]
        profile.save()
        sync_linked_contacts(user, profile)
        AuditLog.objects.create(
            actor=user,
            action="account.profile_updated",
            entity_type="Profile",
            entity_id=str(profile.pk),
            after={
                "preferred_contact_method": profile.preferred_contact_method,
                "timezone": profile.timezone,
                "organization_name": profile.organization_name,
            },
        )
        messages.success(request, "Your account details are saved.")
        return redirect("account_profile")

    def render(self, request, profile, profile_form, password_form=None):
        user = request.user
        if password_form is None:
            password_form = PasswordChangeForm(user)
        return render(
            request,
            self.template_name,
            {
                "profile_form": profile_form,
                "password_form": password_form,
                "account_email": user.email,
                "intro": ROLE_INTRO.get(
                    user.role,
                    "Keep the details we use when we contact you up to date.",
                ),
                "show_organization": user.role != CustomUser.Role.STUDENT,
            },
        )

from typing import Any

from django import forms
from django.utils import timezone

from apps.meetings.access import teacher_families
from apps.meetings.models import FamilyMeeting
from apps.users.models import CustomUser, GuardianRelationship


class FamilyChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj: GuardianRelationship) -> str:
        return f"{obj.child} — {obj.guardian} ({obj.guardian.email})"


class MeetingForm(forms.Form):
    family = FamilyChoice(queryset=GuardianRelationship.objects.none(), label="Reader and invited parent")
    starts_at = forms.DateTimeField(label="Starts (Eastern Time)", widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    ends_at = forms.DateTimeField(label="Ends (Eastern Time)", widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    location = forms.CharField(max_length=400, label="Room, address, or video link")
    revision = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, teacher: CustomUser, *args: Any, meeting: FamilyMeeting | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["family"].queryset = teacher_families(teacher)
        if meeting:
            self.fields["family"].disabled = True
            self.fields["revision"].required = True
            self.initial.update(family=meeting.family_id, revision=meeting.revision,
                                starts_at=timezone.localtime(meeting.starts_at), ends_at=timezone.localtime(meeting.ends_at),
                                location=meeting.location)

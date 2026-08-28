from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

from apps.core.models import RecruitingInterest


MAX_RECRUITING_DOCUMENT_SIZE = 10 * 1024 * 1024
RECRUITING_DOCUMENT_EXTENSIONS = ("pdf", "doc", "docx")


def validate_recruiting_document(uploaded_file):
    FileExtensionValidator(RECRUITING_DOCUMENT_EXTENSIONS)(uploaded_file)
    if uploaded_file.size > MAX_RECRUITING_DOCUMENT_SIZE:
        raise ValidationError("Upload a document that is 10 MB or smaller.")


class RecruitingInterestForm(forms.Form):
    name = forms.CharField(max_length=255)
    phone = forms.CharField(max_length=32)
    address = forms.CharField(max_length=500)
    email = forms.EmailField(max_length=254)
    resume = forms.FileField(validators=(validate_recruiting_document,))
    cover_letter = forms.FileField(validators=(validate_recruiting_document,))
    how_heard = forms.CharField(max_length=255)
    career_path = forms.ChoiceField(choices=RecruitingInterest.CareerPath.choices)

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

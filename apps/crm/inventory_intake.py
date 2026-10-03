"""Public inventory intake, isolated from existing private invitation URLs."""

import uuid
from datetime import timedelta

from django import forms
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from apps.core.bot_protection import HUMAN_MESSAGE, human_check_ok
from apps.crm.consultation_booking import client_key
from apps.crm.inventory import GRADES, log_activity, token_for
from apps.crm.inventory_models import (
    InventoryChild,
    InventoryInvitation,
    InventoryShareLink,
)
from apps.crm.models import Lead
from apps.crm.views import CrmAccessMixin

NONCE_SALT = "inventory-public-intake"


class InventoryIntakeForm(forms.Form):
    first_name = forms.CharField(
        max_length=120,
        label="Your first name",
        widget=forms.TextInput(attrs={"autocomplete": "given-name"}),
    )
    last_name = forms.CharField(
        max_length=120,
        label="Your last name",
        widget=forms.TextInput(attrs={"autocomplete": "family-name"}),
    )
    email = forms.EmailField(
        max_length=254,
        label="Your email",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    child_name = forms.CharField(max_length=120, label="Child’s name")
    age = forms.IntegerField(min_value=3, max_value=21, label="Child’s age in years")
    grade = forms.ChoiceField(
        label="Child’s current school grade",
        choices=[
            ("", "Choose a grade"),
            *[(key, value["label"]) for key, value in GRADES.items()],
        ],
    )
    website = forms.CharField(required=False, widget=forms.HiddenInput)
    nonce = forms.CharField(widget=forms.HiddenInput)

    def clean_email(self) -> str:
        return str(self.cleaned_data["email"]).lower()


@method_decorator(never_cache, name="dispatch")
class InventoryLinkView(CrmAccessMixin, View):
    def get(self, request: HttpRequest) -> HttpResponse:
        link = get_object_or_404(InventoryShareLink, pk=1)
        url = settings.PUBLIC_APP_URL.rstrip("/") + reverse(
            "inventory_intake", args=[link.token]
        )
        return render(request, "crm/inventory_link.html", {"share_url": url})

    def post(self, request: HttpRequest) -> HttpResponse:
        InventoryShareLink.objects.get_or_create(
            pk=1, defaults={"created_by_id": request.user.pk}
        )
        return redirect("inventory_link")


@method_decorator(never_cache, name="dispatch")
class InventoryIntakeView(View):
    def dispatch(
        self, request: HttpRequest, *args: object, **kwargs: object
    ) -> HttpResponseBase:
        response = super().dispatch(request, *args, **kwargs)
        response["Referrer-Policy"] = "same-origin"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response

    def get(self, request: HttpRequest, token: uuid.UUID) -> HttpResponse:
        get_object_or_404(InventoryShareLink, token=token)
        nonce = signing.dumps(
            {"link": str(token), "invitation": str(uuid.uuid4())}, salt=NONCE_SALT
        )
        return render(
            request,
            "crm/inventory_intake.html",
            {"form": InventoryIntakeForm(initial={"nonce": nonce})},
        )

    def post(self, request: HttpRequest, token: uuid.UUID) -> HttpResponse:
        get_object_or_404(InventoryShareLink, token=token)
        key = "inventory-intake:" + client_key(request)
        cache.add(key, 0, 3600)
        if cache.incr(key) > 20:
            return HttpResponse(
                "Too many attempts. Please try again in an hour.", status=429
            )
        if not human_check_ok(request, "inventory-intake"):
            return HttpResponse(HUMAN_MESSAGE, status=400)
        form = InventoryIntakeForm(request.POST)
        if not form.is_valid():
            return render(request, "crm/inventory_intake.html", {"form": form})
        data = form.cleaned_data
        if data["website"]:
            return HttpResponse("Unable to start this inventory.", status=400)
        try:
            payload = signing.loads(data["nonce"], salt=NONCE_SALT, max_age=86400)
            if payload["link"] != str(token):
                raise signing.BadSignature()
            invitation_id = uuid.UUID(payload["invitation"])
        except (signing.BadSignature, KeyError, ValueError, TypeError):
            form.add_error(None, "This form expired. Reload this page and try again.")
            return render(
                request, "crm/inventory_intake.html", {"form": form}, status=400
            )
        with transaction.atomic():
            link = InventoryShareLink.objects.select_for_update().get(token=token)
            invitation = InventoryInvitation.objects.filter(pk=invitation_id).first()
            if invitation is None:
                parent = Lead.objects.filter(
                    contact_email__iexact=data["email"], is_deleted=False
                ).first()
                if parent is None:
                    parent = Lead.objects.create(
                        contact_name=f"{data['first_name']} {data['last_name']}",
                        contact_email=data["email"],
                        school_name="Family",
                        audience=Lead.PipelineCategory.FAMILY_ENROLLMENT,
                        source=Lead.Source.WEBSITE,
                        metadata={
                            "source_path": reverse("inventory_intake", args=[token]),
                            "first_name": data["first_name"],
                            "last_name": data["last_name"],
                        },
                    )
                child = InventoryChild.objects.create(
                    parent=parent,
                    name=data["child_name"],
                    grade=data["grade"],
                    age=data["age"],
                )
                invitation = InventoryInvitation.objects.create(
                    id=invitation_id,
                    child=child,
                    recipient=data["email"],
                    created_by=link.created_by,
                    started_at=timezone.now(),
                    expires_at=timezone.now() + timedelta(days=30),
                )
                log_activity(
                    invitation,
                    f"Started from shared link by {data['first_name']} {data['last_name']} · age {data['age']}",
                )
            if (
                invitation.child.parent.is_deleted
                or invitation.revoked_at
                or invitation.expires_at <= timezone.now()
            ):
                raise Http404("Inventory unavailable")
        return redirect("inventory_public", token=token_for(invitation))

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_tenants.utils import get_tenant_model, tenant_context

from apps.api.permissions import has_coppa_consent
from apps.progress.models import OgConceptResources, OgFamilyPlan
from apps.progress.og_weekly import _drive_resource
from apps.users.models import ChildProfile


class Command(BaseCommand):
    help = "Provision a verified reader's OG+ concept plan and a concept resource pack."

    def add_arguments(self, parser):
        parser.add_argument("--schema", required=True)
        parser.add_argument("--child-id", type=int, required=True)
        parser.add_argument("--first", type=int, default=26)
        parser.add_argument("--last", type=int, default=83)
        parser.add_argument("--concept", type=int, help="Concept for the resource pack; defaults to --first.")
        parser.add_argument("--skip", type=int, action="append", default=[])
        parser.add_argument("--letter", required=True)
        parser.add_argument("--fluency", required=True)
        parser.add_argument("--passage", required=True)

    def handle(self, *args, **options):
        school = get_tenant_model().objects.filter(schema_name=options["schema"], is_deleted=False).first()
        if not school:
            raise CommandError("No active school matches that schema.")
        first, last, skip = options["first"], options["last"], sorted(set(options["skip"]))
        if not (1 <= first <= last <= 83) or any(not first < n <= last for n in skip):
            raise CommandError("Select a concept range within 1–83 and skip only later concepts in range.")
        concept = options["concept"] or first
        if concept not in set(range(first, last + 1)) - set(skip):
            raise CommandError("The resource pack must be for a concept on this plan.")
        links = {
            "connection_letter": options["letter"],
            "fluency_practice": options["fluency"],
            "decodable_passage": options["passage"],
        }
        if len(set(links.values())) != 3 or any(
            len(url) > 500 or not _drive_resource(url) for url in links.values()
        ):
            raise CommandError("Provide three distinct HTTPS Google Drive file URLs.")
        with tenant_context(school), transaction.atomic():
            child = ChildProfile.objects.select_for_update().filter(
                pk=options["child_id"], school=school, is_deleted=False,
            ).first()
            if not child or not has_coppa_consent(child):
                raise CommandError("Verified reader and active guardian consent are required.")
            settings = {"first_concept": first, "last_concept": last, "excluded_concepts": skip}
            plan, _ = OgFamilyPlan.objects.get_or_create(child=child, defaults=settings)
            if any(getattr(plan, field) != expected for field, expected in settings.items()):
                raise CommandError("Existing plan differs; review it before replacing any settings.")
            pack, _ = OgConceptResources.objects.get_or_create(plan=plan, concept=concept, defaults=links)
            if any(getattr(pack, field) != expected for field, expected in links.items()):
                raise CommandError("Existing resources differ; review them before replacing links.")
        self.stdout.write(self.style.SUCCESS(f"OG+ family plan verified for reader ID {child.pk}."))

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import tenant_context

from apps.curriculum.models import PlacementEvidence
from apps.curriculum.placement import generate_recommendation
from apps.schools.models import School


class Command(BaseCommand):
    help = "Generate a reproducible recommendation from completed curriculum-specific placement evidence."

    def add_arguments(self, parser):
        parser.add_argument("--evidence-id", type=int, required=True)
        parser.add_argument("--center-schema", required=True)

    def handle(self, *args, **options):
        center = School.objects.filter(
            schema_name=options["center_schema"],
            is_deleted=False,
        ).first()
        if center is None:
            raise CommandError("Center schema was not found.")
        with tenant_context(center):
            evidence = (
                PlacementEvidence.objects.select_related("center", "child", "curriculum")
                .filter(pk=options["evidence_id"], center=center, is_deleted=False)
                .first()
            )
            if evidence is None:
                raise CommandError("Placement evidence was not found in that center.")
            if evidence.status != PlacementEvidence.Status.COMPLETED:
                raise CommandError("Placement evidence must be completed before recommendation generation.")
            recommendation = generate_recommendation(evidence)
            position_code = recommendation.recommended_position.code if recommendation.recommended_position else "review"
            self.stdout.write(
                self.style.SUCCESS(
                    f"Recommendation {recommendation.id}: {recommendation.decision} at {position_code}"
                )
            )

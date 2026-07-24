from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import tenant_context

from apps.decision_support.interfaces import get_decision_support_engine
from apps.schools.models import School


class Command(BaseCommand):
    help = "Run advisory decision support for a completed session or a placed child."

    def add_arguments(self, parser):
        parser.add_argument("--center-schema", required=True)
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--session-id", type=int)
        target.add_argument("--child-id", type=int)

    def handle(self, *args, **options):
        center = School.objects.filter(schema_name=options["center_schema"], is_deleted=False).first()
        if center is None:
            raise CommandError("Center schema was not found.")
        with tenant_context(center):
            engine = get_decision_support_engine()
            if options["session_id"]:
                flags = engine.evaluate_completed_session(options["session_id"])
                self.stdout.write(self.style.SUCCESS(f"Generated or refreshed {len(flags)} growth flag(s)."))
            else:
                try:
                    prediction = engine.generate_milestone_prediction(options["child_id"])
                except ValueError as error:
                    raise CommandError(str(error)) from error
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Prediction {prediction.id}: {prediction.predicted_sessions} sessions, "
                        f"estimated {prediction.predicted_date}."
                    )
                )

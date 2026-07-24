from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.outcomes.models import DeIdentifiedOutcomeSnapshot
from apps.outcomes.services import aggregate_outcomes, parse_window


class Command(BaseCommand):
    help = "Aggregate de-identified outcomes snapshots for leadership and Foundation reporting."

    def add_arguments(self, parser):
        parser.add_argument(
            "--window-type",
            choices=[choice for choice, _ in DeIdentifiedOutcomeSnapshot.WindowType.choices],
            default=DeIdentifiedOutcomeSnapshot.WindowType.QUARTER,
        )
        parser.add_argument("--start", help="YYYY-MM-DD. Required for month/year/custom; optional for quarter.")
        parser.add_argument("--end", help="YYYY-MM-DD. Required only for custom windows.")
        parser.add_argument("--aggregate-version", default="v1", help="Immutable aggregate version. Reuse is idempotent.")
        parser.add_argument("--metric-scope", default="core_outcomes")
        parser.add_argument("--center", help="Optional center ID or slug.")
        parser.add_argument("--days", type=int, help="Aggregate the inclusive trailing number of days.")
        parser.add_argument("--min-cohort-size", type=int, help="Override the configured privacy floor.")

    def handle(self, *args, **options):
        try:
            start = _parse_date(options.get("start"))
            end = _parse_date(options.get("end"))
            if options.get("days"):
                if start or end:
                    raise ValueError("--days cannot be combined with --start or --end.")
                if options["days"] < 1:
                    raise ValueError("--days must be at least 1.")
                end = timezone.localdate()
                start = end - timedelta(days=options["days"] - 1)
                window = parse_window(DeIdentifiedOutcomeSnapshot.WindowType.CUSTOM, start=start, end=end)
            else:
                window = parse_window(options["window_type"], start=start, end=end)
            snapshots = aggregate_outcomes(
                window=window,
                aggregate_version=options["aggregate_version"],
                metric_scope=options["metric_scope"],
                center=options.get("center"),
                min_cohort_size=options.get("min_cohort_size"),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Outcomes aggregation complete: {len(snapshots)} snapshots for "
                f"{window.window_type} {window.start} through {window.end} "
                f"(version={options['aggregate_version']})."
            )
        )


def _parse_date(value):
    if not value:
        return None
    return date.fromisoformat(value)

from __future__ import annotations

from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.social.crypto import encrypt_text
from apps.social.insights import sync_due_insights, sync_publication
from apps.social.insights_api import (
    InsightsError,
    MetricResult,
    fetch_metrics,
    insight_value,
    request_metrics,
)
from apps.social.meta import facebook_authorization_url, instagram_authorization_url
from apps.social.models import (
    InsightSnapshot,
    PublicationInsights,
    SocialAccount,
    SocialPost,
    SocialPublication,
)
from apps.social.reporting import performance_row, publications_in_range, summarize
from apps.social.tests import META
from apps.users.models import CustomUser


@override_settings(**META)
class InsightsTests(TestCase):
    def setUp(self) -> None:
        self.admin = CustomUser.objects.create_user(
            username="insights-admin", role=CustomUser.Role.SUPER_ADMIN
        )
        self.client.force_login(self.admin)
        self.account = SocialAccount.objects.create(
            network="facebook",
            status="connected",
            external_id="123",
            encrypted_token=encrypt_text("private-test-token"),
        )
        self.post = SocialPost.objects.create(
            facebook_caption="A resource to share", status="posted"
        )
        self.publication = SocialPublication.objects.create(
            post=self.post,
            network="facebook",
            status="published",
            external_id="123_456",
            published_at=timezone.now() - timedelta(days=8),
        )

    def test_sync_records_zero_and_daily_snapshot_without_duplicate_requests(
        self,
    ) -> None:
        with patch(
            "apps.social.insights.fetch_metrics",
            return_value=MetricResult(
                {"views": 40, "likes": 0}, {"reach": "Unavailable"}
            ),
        ) as fetch:
            self.assertTrue(sync_publication(self.publication.pk))
            self.assertFalse(sync_publication(self.publication.pk))
        fetch.assert_called_once()
        state = self.publication.insights
        self.assertEqual(state.metrics, {"views": 40, "likes": 0})
        self.assertEqual(InsightSnapshot.objects.count(), 1)
        row = performance_row(self.publication)
        self.assertEqual(row.values["likes"], 0)
        self.assertNotIn("reach", row.values)

    def test_failed_sync_preserves_last_counts_and_applies_account_circuit(
        self,
    ) -> None:
        state = PublicationInsights.objects.create(
            publication=self.publication, metrics={"views": 99}
        )
        with patch(
            "apps.social.insights.fetch_metrics",
            side_effect=InsightsError("Reconnect analytics", "permission"),
        ):
            self.assertFalse(sync_publication(self.publication.pk))
        state.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(state.metrics["views"], 99)
        self.assertEqual(state.failures, 1)
        self.assertIsNotNone(self.account.insights_retry_after)
        with patch("apps.social.insights.fetch_metrics") as fetch:
            self.assertEqual(sync_due_insights(), 0)
        fetch.assert_not_called()

    def test_active_lease_blocks_another_collector(self) -> None:
        PublicationInsights.objects.create(
            publication=self.publication,
            lease_until=timezone.now() + timedelta(minutes=1),
        )
        with patch("apps.social.insights.fetch_metrics") as fetch:
            self.assertFalse(sync_publication(self.publication.pk))
        fetch.assert_not_called()

    def test_partial_permission_result_keeps_basic_counts(self) -> None:
        with patch(
            "apps.social.insights_api.request_metrics",
            side_effect=[
                {"reactions": {"summary": {"total_count": 5}}},
                InsightsError("Reconnect analytics", "permission"),
            ],
        ):
            result = fetch_metrics(self.publication, self.account)
        self.assertEqual(result.values, {"likes": 5})
        self.assertEqual(result.retry_kind, "permission")
        self.assertIn("views", result.unavailable)
        with patch("apps.social.insights.fetch_metrics", return_value=result):
            sync_publication(self.publication.pk)
        self.assertEqual(self.publication.insights.metrics["likes"], 5)
        self.assertEqual(self.publication.insights.last_error, "Reconnect analytics")

    def test_provider_payloads_and_unsupported_metrics(self) -> None:
        replies = [
            {"reactions": {"summary": {"total_count": 7}}},
            {"comments": {"summary": {"total_count": 2}}},
            {"shares": {"count": 0}},
            {"data": [{"name": "post_media_view", "values": [{"value": 100}]}]},
            InsightsError("Unavailable", "unsupported"),
            {
                "data": [
                    {
                        "name": "post_clicks_by_type",
                        "values": [{"value": {"link clicks": 3, "other clicks": 50}}],
                    }
                ]
            },
        ]
        with patch("apps.social.insights_api.request_metrics", side_effect=replies):
            result = fetch_metrics(self.publication, self.account)
        self.assertEqual(
            result.values,
            {"likes": 7, "comments": 2, "shares": 0, "views": 100, "clicks": 3},
        )
        self.assertNotIn("reach", result.values)
        self.assertEqual(
            insight_value(
                {"data": [{"name": "views", "total_value": {"value": 12}}]}, "views"
            ),
            12,
        )
        self.assertIsNone(insight_value({"data": []}, "views"))
        self.assertIsNone(
            insight_value(
                {"data": [{"name": "views", "values": [{"value": True}]}]}, "views"
            )
        )

    def test_instagram_login_uses_correct_host_and_metrics(self) -> None:
        self.publication.network = "instagram"
        self.account.via_facebook = False
        with patch(
            "apps.social.insights_api.request_metrics",
            return_value={"like_count": 1, "comments_count": 2},
        ) as request:
            result = fetch_metrics(self.publication, self.account)
        self.assertTrue(
            request.call_args_list[0].args[0].startswith("https://graph.instagram.com/")
        )
        self.assertEqual(result.values, {"likes": 1, "comments": 2})
        self.account.via_facebook = True
        with patch(
            "apps.social.insights_api.request_metrics", return_value={}
        ) as request:
            fetch_metrics(self.publication, self.account)
        self.assertTrue(
            request.call_args_list[0].args[0].startswith("https://graph.facebook.com/")
        )

    def test_timeout_rate_limit_auth_and_malformed_response(self) -> None:
        for status, payload, kind in [
            (429, {}, "rate_limit"),
            (403, {}, "permission"),
            (400, {"error": {"code": 190}}, "permission"),
            (503, {}, "temporary"),
            (400, {}, "unsupported"),
        ]:
            with (
                self.subTest(status=status, payload=payload),
                patch(
                    "apps.social.insights_api.requests.get",
                    return_value=Mock(
                        status_code=status, json=Mock(return_value=payload)
                    ),
                ),
            ):
                with self.assertRaises(InsightsError) as raised:
                    request_metrics("https://graph.facebook.com/test", "secret", {})
                self.assertEqual(raised.exception.kind, kind)
        with (
            patch(
                "apps.social.insights_api.requests.get", side_effect=requests.Timeout
            ),
            self.assertRaises(InsightsError),
        ):
            request_metrics("https://graph.facebook.com/test", "secret", {})
        with (
            patch(
                "apps.social.insights_api.requests.get",
                return_value=Mock(status_code=200, json=Mock(side_effect=ValueError)),
            ),
            self.assertRaises(InsightsError),
        ):
            request_metrics("https://graph.facebook.com/test", "secret", {})

    def test_analytics_permissions_are_opt_in(self) -> None:
        self.assertNotIn("read_insights", facebook_authorization_url("state"))
        self.assertIn(
            "read_insights", facebook_authorization_url("state", insights=True)
        )
        self.assertNotIn("manage_insights", instagram_authorization_url("state"))
        self.assertIn(
            "instagram_business_manage_insights",
            instagram_authorization_url("state", insights=True),
        )

    def test_report_counts_content_once_keeps_platforms_separate_and_exports_safely(
        self,
    ) -> None:
        self.post.facebook_caption = "=FORMULA()"
        self.post.save()
        PublicationInsights.objects.create(
            publication=self.publication, metrics={"views": 10, "likes": 0}
        )
        SocialPublication.objects.create(
            post=self.post,
            network="instagram",
            status="published",
            external_id="789",
            published_at=self.publication.published_at,
        )
        response = self.client.get(reverse("social:performance"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["content_count"], 1)
        self.assertEqual(response.context["publication_count"], 2)
        self.assertEqual(len(response.context["summaries"]), 2)
        self.assertContains(response, "Unavailable")
        export = self.client.get(reverse("social:performance"), {"export": "csv"})
        self.assertIn("'=FORMULA()", export.content.decode())
        self.assertNotIn("secret", export.content.decode())

    def test_seven_day_comparison_only_uses_actual_window(self) -> None:
        published = self.publication.published_at
        InsightSnapshot.objects.create(
            publication=self.publication,
            day=(published + timedelta(days=7)).date(),
            captured_at=published + timedelta(days=7, hours=2),
            metrics={"views": 20, "reach": 10},
        )
        InsightSnapshot.objects.create(
            publication=self.publication,
            day=(published + timedelta(days=9)).date(),
            captured_at=published + timedelta(days=9),
            metrics={"views": 1000},
        )
        today = timezone.localdate()
        publication = publications_in_range(
            today - timedelta(days=30), today, "", ""
        ).get(pk=self.publication.pk)
        row = performance_row(publication, week=True)
        self.assertEqual(row.values["views"], 20)
        group = summarize([row])[0]
        self.assertEqual(group.average_views, "20")
        self.assertNotIn("reach", group.totals)

    def test_missing_connection_is_explicit_and_no_request_is_sent(self) -> None:
        self.account.delete()
        with patch("apps.social.insights.fetch_metrics") as fetch:
            self.assertFalse(sync_publication(self.publication.pk))
        fetch.assert_not_called()
        self.assertIn("Not connected", self.publication.insights.last_error)

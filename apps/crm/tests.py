from django.test import SimpleTestCase

from apps.crm.models import Lead, Opportunity
from apps.crm.serializers import OpportunitySerializer
from apps.crm.views import WebsiteSignupView


class CrmTests(SimpleTestCase):
    def test_lead_pipeline_statuses_exist(self):
        self.assertIn(Lead.Status.NEW, Lead.Status.values)
        self.assertIn(Lead.Status.CONVERTED, Lead.Status.values)

    def test_opportunity_terminal_stages_exist(self):
        self.assertIn(Opportunity.Stage.WON, Opportunity.Stage.values)
        self.assertIn(Opportunity.Stage.LOST, Opportunity.Stage.values)

    def test_opportunity_probability_validation(self):
        serializer = OpportunitySerializer()
        with self.assertRaisesMessage(Exception, "Probability must be between 0 and 100."):
            serializer.validate_probability(101)

    def test_website_signup_defaults_family_inquiry_for_parent(self):
        self.assertEqual(
            WebsiteSignupView._school_name_for_signup(Lead.Audience.PARENT, ""),
            "Family inquiry",
        )

    def test_website_signup_cleans_positive_student_count(self):
        self.assertEqual(WebsiteSignupView._clean_positive_int("24"), 24)
        self.assertIsNone(WebsiteSignupView._clean_positive_int("-1"))
        self.assertIsNone(WebsiteSignupView._clean_positive_int("many"))

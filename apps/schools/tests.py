from django.test import SimpleTestCase

from apps.schools.models import School, SchoolMembership
from apps.schools.serializers import SchoolSerializer


class SchoolsTests(SimpleTestCase):
    def test_school_is_tenant_model(self):
        self.assertTrue(School.auto_create_schema)
        self.assertFalse(School.auto_drop_schema)

    def test_membership_admin_role_exists(self):
        self.assertIn(SchoolMembership.Role.ADMIN, SchoolMembership.Role.values)

    def test_school_serializer_exposes_onboarding_fields(self):
        fields = SchoolSerializer().fields
        self.assertIn("schema_name", fields)
        self.assertIn("branding", fields)

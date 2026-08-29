from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve

from apps.users.models import CustomUser


class PortalNavigationTests(SimpleTestCase):
    def setUp(self):
        self.request_factory = RequestFactory()

    def render_header(self, *, role, is_staff=False, path="/dashboard/"):
        request = self.request_factory.get(path)
        request.user = CustomUser(
            email=f"{role}@example.com",
            role=role,
            is_staff=is_staff,
        )
        request.resolver_match = resolve(path)
        return render_to_string("portal/_header.html", request=request)

    def test_administrator_gets_horizontal_program_management_and_business_menus(self):
        content = self.render_header(role=CustomUser.Role.SUPER_ADMIN, is_staff=True)

        self.assertIn('data-testid="program-menu-button"', content)
        self.assertIn('data-testid="manage-menu-button"', content)
        self.assertIn('data-testid="business-menu-button"', content)
        self.assertIn('aria-controls="program-navigation-panel"', content)
        self.assertIn('href="/dashboard/#admin-actions"', content)
        self.assertIn('data-testid="crm-header-link"', content)
        self.assertNotIn('data-testid="teaching-menu-button"', content)
        self.assertNotIn('role="menu"', content)

    def test_teacher_gets_teaching_menu_without_administrator_tools(self):
        content = self.render_header(role=CustomUser.Role.TEACHER)

        self.assertIn('data-testid="teaching-menu-button"', content)
        self.assertIn('href="/portal/sessions/rapid-log/"', content)
        self.assertIn('href="/dashboard/#assessment-review"', content)
        self.assertNotIn('data-testid="business-menu-button"', content)
        self.assertNotIn('data-testid="crm-header-link"', content)

    def test_parent_gets_family_menu_without_staff_destinations(self):
        content = self.render_header(role=CustomUser.Role.GUARDIAN)

        self.assertIn('data-testid="family-menu-button"', content)
        self.assertIn('href="/dashboard/#progress-overview"', content)
        self.assertIn('href="/dashboard/#teacher-plan"', content)
        self.assertNotIn('data-testid="teaching-menu-button"', content)
        self.assertNotIn("Django admin", content)

    def test_every_disclosure_exposes_state_and_keyboard_dismissal(self):
        content = self.render_header(role=CustomUser.Role.SUPER_ADMIN, is_staff=True)

        self.assertEqual(content.count('aria-expanded="false"'), 4)
        self.assertIn("event.key !== 'Escape'", content)
        self.assertIn("if (!header.contains(document.activeElement)) closeAll()", content)

    def test_dashboard_and_session_log_expose_shared_navigation_destinations(self):
        dashboard = Path(settings.BASE_DIR, "templates/portal/dashboard.html").read_text()
        rapid_log = Path(settings.BASE_DIR, "templates/sessions/rapid_log.html").read_text()

        for destination in (
            "progress-overview",
            "session-launchpad",
            "placement-review",
            "admin-actions",
            "lesson-library",
            "website-signups",
            "account-creation",
            "lesson-planning",
            "assessment-review",
            "teacher-plan",
            "latest-kpis",
        ):
            self.assertIn(f'id="{destination}"', dashboard)
        self.assertIn('{% include "portal/_header.html" %}', rapid_log)

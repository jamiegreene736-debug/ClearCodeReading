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

    def test_administrator_gets_program_and_manage_menus_plus_a_crm_link(self):
        content = self.render_header(role=CustomUser.Role.SUPER_ADMIN, is_staff=True)

        self.assertIn('data-testid="program-menu-button"', content)
        self.assertIn('data-testid="manage-menu-button"', content)
        self.assertNotIn('data-testid="business-menu-button"', content)
        self.assertNotIn("Business tools", content)
        self.assertIn('data-testid="crm-header-link"', content)
        self.assertIn(">CRM</a>", content)
        self.assertIn('aria-controls="program-navigation-panel"', content)
        self.assertIn('data-testid="teacher-assignments-menu-link"', content)
        self.assertIn('href="/portal/teacher-assignments/"', content)
        self.assertIn("Pair each reader with the teacher who leads their instruction", content)
        self.assertIn('data-testid="lesson-library-menu-link"', content)
        self.assertIn('href="/portal/lesson-library/"', content)
        self.assertIn("Create lessons and share them with the teachers who will use them", content)
        self.assertNotIn('href="/dashboard/#admin-actions"', content)
        self.assertNotIn('href="/dashboard/#lesson-library"', content)
        self.assertNotIn('href="/dashboard/#website-signups"', content)
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

        self.assertEqual(content.count('aria-expanded="false"'), 3)
        self.assertIn('data-testid="account-profile-link"', content)
        self.assertIn('href="/account/"', content)
        self.assertIn("event.key !== 'Escape'", content)
        self.assertIn("if (!header.contains(document.activeElement)) closeAll()", content)

    def test_account_menu_uses_first_name_then_account(self):
        request = self.request_factory.get("/dashboard/")
        request.user = CustomUser(
            email="jamie@example.com",
            role=CustomUser.Role.GUARDIAN,
            first_name="Jamie",
        )
        request.resolver_match = resolve("/dashboard/")
        content = render_to_string("portal/_header.html", request=request)

        self.assertIn('data-testid="account-menu-name"', content)
        self.assertIn("Jamie", content)
        self.assertIn(">Account<", content)

    def test_dashboard_and_session_log_expose_shared_navigation_destinations(self):
        dashboard = Path(settings.BASE_DIR, "templates/portal/dashboard.html").read_text()
        admin_workspace = Path(settings.BASE_DIR, "templates/portal/_admin_workspace.html").read_text()
        teacher_assignments = Path(settings.BASE_DIR, "templates/portal/teacher_assignments.html").read_text()
        lesson_library = Path(settings.BASE_DIR, "templates/portal/lesson_library.html").read_text()
        rapid_log = Path(settings.BASE_DIR, "templates/sessions/rapid_log.html").read_text()
        portal_markup = dashboard + admin_workspace

        for destination in (
            "progress-overview",
            "session-launchpad",
            "placement-review",
            "lesson-planning",
            "assessment-review",
            "teacher-plan",
            "latest-kpis",
        ):
            self.assertIn(f'id="{destination}"', portal_markup)
        self.assertIn('id="admin-actions"', teacher_assignments)
        self.assertIn('id="lesson-library"', lesson_library)
        self.assertIn("{% url 'portal_teacher_assignments' %}", admin_workspace)
        self.assertIn("{% url 'portal_lesson_library' %}", admin_workspace)
        self.assertNotIn('id="admin-actions"', admin_workspace)
        self.assertNotIn("{% url 'assign_teacher' %}", admin_workspace)
        self.assertNotIn('id="website-signups"', portal_markup)
        self.assertNotIn("New signups", admin_workspace)
        self.assertNotIn('id="account-creation"', portal_markup)
        self.assertNotIn("Specialist launchpad", admin_workspace)
        self.assertNotIn("KPI areas", admin_workspace)
        self.assertIn('aria-label="Program status"', admin_workspace)
        self.assertIn('{% include "portal/_header.html" %}', rapid_log)

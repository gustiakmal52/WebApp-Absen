import uuid
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from django.contrib import admin
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from .crypto import seal
from .admin import AttendanceAdmin
from .face import FaceError, verify_sequence
from .forms import LeaveRequestForm
from .models import Attendance, AttendanceEvent, Employee, FaceEnrollment, FaceProfile, LeaveRequest, LivenessChallenge, Office
from .services import AttendanceError, haversine_m, make_challenge, record_event


class GenericInstallTests(TestCase):
    def test_fresh_install_has_no_company_or_office(self):
        self.assertFalse(Office.objects.exists())
        self.assertContains(self.client.get(reverse("login")), "Hadir")


class AttendanceFlowTests(TestCase):
    def setUp(self):
        self.evidence_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.evidence_dir.cleanup)
        evidence_settings = self.settings(EVIDENCE_DIR=Path(self.evidence_dir.name))
        evidence_settings.enable()
        self.addCleanup(evidence_settings.disable)
        now = timezone.localtime()
        self.office = Office.objects.create(
            name="Kantor Test", address="Test", latitude=-5.147665, longitude=119.432732,
            radius_m=150, max_accuracy_m=75, workdays=list(range(7)),
            start_time=(now - timedelta(hours=1)).time(), end_time=(now + timedelta(hours=1)).time(),
        )
        self.user = Employee.objects.create_user(username="KRY-TEST", password="AmanSekali123!", full_name="Karyawan Test", office=self.office)
        FaceProfile.objects.create(employee=self.user, encrypted_embedding=seal(b"embedding"))

    def test_distance_is_zero_for_same_point(self):
        self.assertAlmostEqual(haversine_m(-5.1, 119.4, -5.1, 119.4), 0)

    def test_challenge_is_short_lived_and_ordered(self):
        challenge = make_challenge(self.user)
        self.assertEqual(set(challenge.actions), {"TURN_LEFT", "TURN_RIGHT"})
        self.assertLess((challenge.expires_at - timezone.now()).total_seconds(), 91)

    def test_challenge_preflight_returns_detected_outlet(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("challenge_api"), {
            "latitude": -5.147665,
            "longitude": 119.432732,
            "accuracy_m": 12,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["office"]["name"], self.office.name)

    def test_employee_can_choose_active_outlet_when_registering(self):
        self.client.logout()
        response = self.client.post(reverse("register"), {
            "username": "KRY-BARU",
            "full_name": "Karyawan Baru",
            "password1": "AmanSekali456!",
            "password2": "AmanSekali456!",
            "office": self.office.pk,
        })
        employee = Employee.objects.get(username="KRY-BARU")
        self.assertRedirects(response, reverse("enrollment"))
        self.assertEqual(employee.office, self.office)
        self.assertEqual(employee.role, Employee.Role.EMPLOYEE)
        self.assertFalse(employee.is_staff)

    def test_employee_registration_rejects_inactive_outlet(self):
        self.office.is_active = False
        self.office.save(update_fields=["is_active"])
        response = self.client.post(reverse("register"), {
            "username": "KRY-JAUH",
            "full_name": "Karyawan Jauh",
            "password1": "AmanSekali456!",
            "password2": "AmanSekali456!",
            "office": self.office.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Employee.objects.filter(username="KRY-JAUH").exists())

    def test_registration_can_be_closed_in_production(self):
        with self.settings(ALLOW_SELF_REGISTRATION=False):
            self.assertEqual(self.client.get(reverse("register")).status_code, 404)

    def test_employee_cannot_use_admin_portal(self):
        response = self.client.post(f'{reverse("login")}?portal=admin', {
            "username": self.user.username,
            "password": "AmanSekali123!",
            "portal": "admin",
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_createsuperuser_can_use_admin_portal(self):
        owner = Employee.objects.create_superuser(username="OWNER", password="AmanSekali123!", full_name="Pemilik")
        self.assertEqual(owner.role, Employee.Role.ADMIN)
        response = self.client.post(f'{reverse("login")}?portal=admin', {
            "username": owner.username,
            "password": "AmanSekali123!",
            "portal": "admin",
        })
        self.assertRedirects(response, reverse("dashboard"))

    def test_admin_portal_uses_admin_username_copy(self):
        response = self.client.get(f'{reverse("login")}?portal=admin')
        self.assertContains(response, "Username admin")
        self.assertContains(response, "Contoh: admin")
        self.assertNotContains(response, "Contoh: KRY-001")

    def test_admin_dashboard_has_no_face_or_attendance_approval(self):
        self.user.role = Employee.Role.ADMIN
        self.user.save()
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Rekap absensi")
        self.assertNotContains(response, "Persetujuan wajah")
        self.assertNotContains(response, "MENUNGGU WAJAH")

    def test_approved_history_without_profile_can_reenroll(self):
        self.user.face_profile.delete()
        FaceEnrollment.objects.create(
            employee=self.user,
            candidate_embedding=seal(b"old"),
            status=FaceEnrollment.Status.APPROVED,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("enrollment"))
        self.assertContains(response, "Mulai perekaman")
        self.assertNotContains(response, "Wajah Anda siap digunakan")

    @patch("attendance.views.enrollment_template", return_value=(b"embedding", b"preview"))
    def test_face_enrollment_activates_without_location_check(self, template):
        self.user.face_profile.delete()
        self.client.force_login(self.user)
        response = self.client.post(reverse("enrollment_api"), {
            "consent": "true",
            "frames": [SimpleUploadedFile(f"face-{index}.jpg", b"x", content_type="image/jpeg") for index in range(5)],
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(FaceProfile.objects.filter(employee=self.user).exists())
        enrollment = FaceEnrollment.objects.get(employee=self.user)
        self.assertEqual(enrollment.status, FaceEnrollment.Status.APPROVED)
        self.assertEqual(enrollment.candidate_embedding, b"")

    def test_purge_clears_legacy_duplicate_biometric_template(self):
        enrollment = FaceEnrollment.objects.create(employee=self.user, candidate_embedding=seal(b"old"), status=FaceEnrollment.Status.APPROVED)
        call_command("purge_evidence", verbosity=0)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.candidate_embedding, b"")
        self.assertTrue(FaceProfile.objects.filter(employee=self.user).exists())

    def test_rejects_inaccurate_location_before_face_processing(self):
        challenge = make_challenge(self.user)
        with self.assertRaises(AttendanceError) as caught:
            record_event(self.user, uuid.uuid4(), challenge.id, AttendanceEvent.Kind.CHECK_IN, -5.147665, 119.432732, 200, [b"x"] * 8)
        self.assertEqual(caught.exception.code, "GPS_INACCURATE")

    def test_rejects_non_finite_location(self):
        with self.assertRaises(AttendanceError) as caught:
            record_event(self.user, uuid.uuid4(), make_challenge(self.user).id, AttendanceEvent.Kind.CHECK_IN, float("nan"), 119.432732, 12, [b"x"] * 8)
        self.assertEqual(caught.exception.code, "INVALID_LOCATION")

    @patch("attendance.services.verify_sequence", return_value=(0.82, b"jpeg"))
    def test_check_in_is_recorded_once(self, verify):
        challenge = make_challenge(self.user)
        event = record_event(self.user, uuid.uuid4(), challenge.id, AttendanceEvent.Kind.CHECK_IN, -5.147665, 119.432732, 12, [b"x"] * 8)
        self.assertEqual(event.kind, AttendanceEvent.Kind.CHECK_IN)
        self.assertTrue(event.evidence_path)

    @patch("attendance.services.verify_sequence", return_value=(0.82, b"jpeg"))
    def test_request_id_cannot_be_reused_for_other_event_or_employee(self, verify):
        request_id = uuid.uuid4()
        event = record_event(self.user, request_id, make_challenge(self.user).id, AttendanceEvent.Kind.CHECK_IN, -5.147665, 119.432732, 12, [b"x"] * 8)
        self.assertEqual(record_event(self.user, request_id, None, AttendanceEvent.Kind.CHECK_IN, 0, 0, 0, []), event)
        with self.assertRaises(AttendanceError) as caught:
            record_event(self.user, request_id, None, AttendanceEvent.Kind.CHECK_OUT, 0, 0, 0, [])
        self.assertEqual(caught.exception.status, 409)
        other = Employee.objects.create_user(username="OTHER", password="AmanSekali123!", full_name="Orang Lain", office=self.office)
        with self.assertRaises(AttendanceError) as caught:
            record_event(other, request_id, None, AttendanceEvent.Kind.CHECK_IN, 0, 0, 0, [])
        self.assertEqual(caught.exception.status, 409)

    def test_face_sequence_rejects_one_mismatched_frame(self):
        recognizer = Mock()
        recognizer.match.side_effect = [0.8] * 7 + [0.1]
        directions = ["LEFT", "RIGHT"] + ["CENTER"] * 6
        frames = [(np.ones(128, dtype=np.float32), direction, None, None) for direction in directions]
        with patch("attendance.face.analyze", side_effect=frames), patch("attendance.face._models", return_value=(None, recognizer)):
            with self.assertRaises(FaceError) as caught:
                verify_sequence([b"frame"] * 8, ["TURN_LEFT", "TURN_RIGHT"], np.ones(128, dtype=np.float32).tobytes())
        self.assertEqual(caught.exception.code, "FACE_MISMATCH")

    @patch("attendance.services.verify_sequence", return_value=(0.82, b"jpeg"))
    def test_check_in_detects_nearest_active_outlet(self, verify):
        assigned = Office.objects.create(
            name="Outlet Lain", address="Jauh", latitude=-5.2, longitude=119.5,
            radius_m=150, max_accuracy_m=75, workdays=list(range(7)),
            start_time=self.office.start_time, end_time=self.office.end_time,
        )
        self.user.office = assigned
        self.user.save(update_fields=["office"])
        event = record_event(
            self.user, uuid.uuid4(), make_challenge(self.user).id,
            AttendanceEvent.Kind.CHECK_IN, -5.147665, 119.432732, 12, [b"x"] * 8,
        )
        self.assertEqual(event.attendance.office, self.office)

    @patch("attendance.services.verify_sequence", return_value=(0.82, b"jpeg"))
    def test_check_out_must_use_same_outlet(self, verify):
        other = Office.objects.create(
            name="Outlet Tetangga", address="Lain", latitude=-5.157665, longitude=119.432732,
            radius_m=150, max_accuracy_m=75, workdays=list(range(7)),
            start_time=self.office.start_time, end_time=self.office.end_time,
        )
        record_event(
            self.user, uuid.uuid4(), make_challenge(self.user).id,
            AttendanceEvent.Kind.CHECK_IN, -5.147665, 119.432732, 12, [b"x"] * 8,
        )
        with self.assertRaises(AttendanceError) as caught:
            record_event(
                self.user, uuid.uuid4(), make_challenge(self.user).id,
                AttendanceEvent.Kind.CHECK_OUT, float(other.latitude), float(other.longitude), 12, [b"x"] * 8,
            )
        self.assertEqual(caught.exception.code, "OFFICE_MISMATCH")

    @patch("attendance.services.verify_sequence", return_value=(0.82, b"jpeg"))
    def test_dashboard_shows_check_out_after_check_in(self, verify):
        record_event(
            self.user, uuid.uuid4(), make_challenge(self.user).id,
            AttendanceEvent.Kind.CHECK_IN, -5.147665, 119.432732, 12, [b"x"] * 8,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Absen pulang")
        self.assertContains(response, 'data-start-attendance="CHECK_OUT"')

    def test_dashboard_keeps_check_out_visible_when_face_is_not_ready(self):
        self.user.face_profile.delete()
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Absen pulang")
        self.assertNotContains(response, 'data-start-attendance="CHECK_OUT"')

    @patch("attendance.services.verify_sequence", return_value=(0.82, b"jpeg"))
    def test_deleting_employee_also_deletes_their_attendance(self, verify):
        record_event(
            self.user, uuid.uuid4(), make_challenge(self.user).id,
            AttendanceEvent.Kind.CHECK_IN, -5.147665, 119.432732, 12, [b"x"] * 8,
        )
        employee_id = self.user.pk
        self.user.delete()
        self.assertFalse(Employee.objects.filter(pk=employee_id).exists())
        self.assertFalse(Attendance.objects.filter(employee_id=employee_id).exists())

    def test_other_employee_cannot_read_evidence(self):
        with patch("attendance.services.verify_sequence", return_value=(0.82, b"jpeg")):
            event = record_event(self.user, uuid.uuid4(), make_challenge(self.user).id, AttendanceEvent.Kind.CHECK_IN, -5.147665, 119.432732, 12, [b"x"] * 8)
        other = Employee.objects.create_user(username="OTHER", password="AmanSekali123!", full_name="Orang Lain", office=self.office)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("evidence", args=[event.pk])).status_code, 403)
        self.client.force_login(self.user)
        response = self.client.get(reverse("evidence", args=[event.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"jpeg")

    def test_attendance_api_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("challenge_api"), {}).status_code, 403)

    @patch("attendance.services.verify_sequence", return_value=(0.82, b"jpeg"))
    def test_csv_export_escapes_formula_from_employee_name(self, verify):
        self.user.full_name = "=1+1"
        self.user.save(update_fields=["full_name"])
        record_event(self.user, uuid.uuid4(), make_challenge(self.user).id, AttendanceEvent.Kind.CHECK_IN, -5.147665, 119.432732, 12, [b"x"] * 8)
        response = AttendanceAdmin(Attendance, admin.site).export_csv(RequestFactory().get("/admin/"), Attendance.objects.all())
        self.assertIn("'=1+1", response.content.decode())

    def test_resigned_employee_cannot_login(self):
        self.user.is_active = False
        self.user.resigned_at = timezone.now()
        self.user.save(update_fields=["is_active", "resigned_at"])
        self.assertFalse(self.client.login(username="KRY-TEST", password="AmanSekali123!"))

    def test_leave_request_creation_and_workflow(self):
        from datetime import date
        leave = LeaveRequest.objects.create(
            employee=self.user,
            kind=LeaveRequest.Kind.SICK,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=2),
            reason="Demam dan flu",
        )
        self.assertEqual(leave.status, LeaveRequest.Status.PENDING)
        self.assertEqual(leave.employee, self.user)
        self.assertEqual(leave.get_kind_display(), "Sakit")

    def test_role_demotion_revokes_admin_access(self):
        self.user.role = Employee.Role.ADMIN
        self.user.save()
        self.assertTrue(self.user.is_staff)
        self.user.role = Employee.Role.EMPLOYEE
        self.user.save()
        self.assertFalse(self.user.is_staff)

    def test_leave_attachment_is_validated_and_private(self):
        invalid = LeaveRequestForm(files={"attachment": SimpleUploadedFile("script.html", b"x")})
        self.assertFalse(invalid.is_valid())
        self.assertIn("attachment", invalid.errors)

        with tempfile.TemporaryDirectory() as upload_dir, self.settings(MEDIA_ROOT=upload_dir):
            self.client.force_login(self.user)
            response = self.client.post(reverse("leave_request"), {
                "kind": LeaveRequest.Kind.SICK,
                "start_date": timezone.localdate(),
                "end_date": timezone.localdate(),
                "reason": "Surat dokter terlampir",
                "attachment": SimpleUploadedFile("surat.pdf", b"%PDF-1.4\n", content_type="application/pdf"),
            })
            self.assertRedirects(response, reverse("dashboard"))
            leave = LeaveRequest.objects.get(employee=self.user)
            owner_url = leave.attachment.url
            self.assertEqual(self.client.get(owner_url).status_code, 200)

            other = Employee.objects.create_user(username="OUTSIDER", password="AmanSekali123!", full_name="Orang Lain", office=self.office)
            self.client.force_login(other)
            self.assertEqual(self.client.get(owner_url).status_code, 403)

import math
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .crypto import open_sealed, seal
from .face import verify_sequence
from .models import Attendance, AttendanceEvent, FaceProfile, Holiday, LivenessChallenge, Office


class AttendanceError(Exception):
    def __init__(self, code, message, status=422):
        self.code, self.status = code, status
        super().__init__(message)


def haversine_m(lat1, lon1, lat2, lon2):
    radius = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def local_now(office):
    return timezone.now().astimezone(ZoneInfo(office.timezone))


def detect_office(latitude, longitude, accuracy):
    if not all(math.isfinite(value) for value in (latitude, longitude, accuracy)) or not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or accuracy <= 0:
        raise AttendanceError("INVALID_LOCATION", "Data lokasi tidak valid.")
    offices = list(Office.objects.filter(is_active=True))
    if not offices:
        raise AttendanceError("OFFICE_REQUIRED", "Belum ada outlet aktif.")
    office, distance = min(
        ((office, haversine_m(latitude, longitude, float(office.latitude), float(office.longitude))) for office in offices),
        key=lambda item: item[1],
    )
    if accuracy > office.max_accuracy_m:
        raise AttendanceError("GPS_INACCURATE", f"Akurasi GPS harus di bawah {office.max_accuracy_m} meter.")
    if distance > office.radius_m:
        raise AttendanceError("GPS_OUTSIDE_RADIUS", f"Lokasi Anda {distance:.0f} m dari outlet terdekat. Batas {office.name} adalah {office.radius_m} m.")
    return office, distance


def make_challenge(employee):
    if not Office.objects.filter(is_active=True).exists():
        raise AttendanceError("OFFICE_REQUIRED", "Belum ada outlet aktif.")
    if not hasattr(employee, "face_profile"):
        raise AttendanceError("ENROLLMENT_REQUIRED", "Daftarkan wajah untuk mengaktifkan absensi.")
    recent = LivenessChallenge.objects.filter(employee=employee, created_at__gte=timezone.now() - timedelta(minutes=1)).count()
    if recent >= 5:
        raise AttendanceError("TOO_MANY_ATTEMPTS", "Terlalu banyak percobaan. Tunggu satu menit.", 429)
    actions = ["TURN_LEFT", "TURN_RIGHT"]
    if uuid.uuid4().int & 1:
        actions.reverse()
    return LivenessChallenge.objects.create(employee=employee, actions=actions, expires_at=timezone.now() + timedelta(seconds=90))


def _event_result(kind, occurred, office):
    local_time = occurred.astimezone(ZoneInfo(office.timezone))
    if kind == AttendanceEvent.Kind.CHECK_IN:
        grace = datetime.combine(local_time.date(), office.start_time, local_time.tzinfo) + timedelta(minutes=office.grace_minutes)
        return AttendanceEvent.Result.ON_TIME if local_time <= grace else AttendanceEvent.Result.LATE
    end = datetime.combine(local_time.date(), office.end_time, local_time.tzinfo)
    return AttendanceEvent.Result.EARLY if local_time < end else AttendanceEvent.Result.COMPLETE


@transaction.atomic
def record_event(employee, request_id, challenge_id, kind, latitude, longitude, accuracy, raw_frames):
    try:
        old = AttendanceEvent.objects.select_related("attendance").get(request_id=request_id)
        if old.attendance.employee_id == employee.id and old.kind == kind:
            return old
        raise AttendanceError("REQUEST_ID_CONFLICT", "ID permintaan sudah dipakai untuk absensi lain.", 409)
    except AttendanceEvent.DoesNotExist:
        pass
    office, distance = detect_office(float(latitude), float(longitude), accuracy)
    now = timezone.now()
    local = now.astimezone(ZoneInfo(office.timezone))
    if local.weekday() not in office.workdays or Holiday.objects.filter(office=office, date=local.date()).exists():
        raise AttendanceError("OFF_DAY", "Hari ini bukan hari kerja di kantor Anda.")
    attendance = Attendance.objects.select_for_update().filter(employee=employee, work_date=local.date()).first()
    if attendance and attendance.office_id != office.id:
        raise AttendanceError("OFFICE_MISMATCH", f"Absensi hari ini terikat ke {attendance.office.name}. Lanjutkan di outlet yang sama.")
    if not attendance and kind == AttendanceEvent.Kind.CHECK_OUT:
        raise AttendanceError("CHECK_IN_REQUIRED", "Anda belum melakukan absen masuk.")
    try:
        challenge = LivenessChallenge.objects.select_for_update().get(id=challenge_id, employee=employee)
    except (LivenessChallenge.DoesNotExist, ValueError):
        raise AttendanceError("CHALLENGE_INVALID", "Challenge tidak valid.")
    if challenge.used_at:
        raise AttendanceError("CHALLENGE_USED", "Challenge ini sudah pernah dipakai.", 409)
    if challenge.expires_at < now:
        raise AttendanceError("CHALLENGE_EXPIRED", "Challenge sudah kedaluwarsa.")
    try:
        profile = employee.face_profile
    except FaceProfile.DoesNotExist:
        raise AttendanceError("ENROLLMENT_REQUIRED", "Profil wajah belum aktif.")
    score, evidence = verify_sequence(raw_frames, challenge.actions, open_sealed(profile.encrypted_embedding))
    if not attendance:
        attendance = Attendance.objects.create(employee=employee, office=office, work_date=local.date())
    if attendance.events.filter(kind=kind).exists():
        raise AttendanceError("DUPLICATE_EVENT", "Absensi ini sudah tercatat.", 409)
    if kind == AttendanceEvent.Kind.CHECK_OUT and not attendance.events.filter(kind=AttendanceEvent.Kind.CHECK_IN).exists():
        raise AttendanceError("CHECK_IN_REQUIRED", "Anda belum melakukan absen masuk.")
    evidence_name = f"{local:%Y/%m}/{uuid.uuid4().hex}.bin"
    path = settings.EVIDENCE_DIR / evidence_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(seal(evidence))
    challenge.used_at = now
    challenge.save(update_fields=["used_at"])
    event = AttendanceEvent.objects.create(
        attendance=attendance, request_id=request_id, challenge=challenge, kind=kind,
        occurred_at=now, result=_event_result(kind, now, office), latitude=latitude,
        longitude=longitude, accuracy_m=accuracy, distance_m=distance,
        face_score=score, evidence_path=evidence_name,
    )
    if kind == AttendanceEvent.Kind.CHECK_OUT:
        attendance.status = Attendance.Status.COMPLETE
        attendance.save(update_fields=["status", "updated_at"])
    return event

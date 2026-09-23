import uuid
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db import connection, transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .crypto import open_sealed, seal
from .face import FaceError, enrollment_template
from .forms import EmployeeRegistrationForm, LeaveRequestForm
from .models import AuditEvent, Attendance, AttendanceEvent, Employee, FaceEnrollment, FaceProfile, LeaveRequest
from .services import AttendanceError, detect_office, local_now, make_challenge, record_event


def _error(exc):
    status = getattr(exc, "status", 422)
    return JsonResponse({"ok": False, "code": getattr(exc, "code", "INVALID_REQUEST"), "message": str(exc)}, status=status)


def _location(request):
    try:
        latitude = float(request.POST["latitude"])
        longitude = float(request.POST["longitude"])
        accuracy = float(request.POST["accuracy_m"])
    except (ValueError, KeyError):
        raise AttendanceError("INVALID_LOCATION", "Data lokasi tidak valid.")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise AttendanceError("INVALID_LOCATION", "Koordinat lokasi tidak valid.")
    return latitude, longitude, accuracy


def register(request):
    if not settings.ALLOW_SELF_REGISTRATION:
        raise Http404
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = EmployeeRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        office = form.cleaned_data["office"]
        employee = form.save(commit=False)
        employee.role = Employee.Role.EMPLOYEE
        employee.must_change_password = False
        employee.save()
        AuditEvent.objects.create(
            actor=employee, action="employee.self_register", object_type="Employee", object_id=str(employee.pk),
            reason="Pendaftaran mandiri dengan pilihan outlet", after={"office_id": office.pk, "office": office.name},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        login(request, employee)
        messages.success(request, f"Akun terdaftar di {office.name}. Sekarang aktifkan profil wajah.")
        return redirect("enrollment")
    return render(request, "registration/register.html", {"form": form})


@login_required
def dashboard(request):
    if request.user.role == request.user.Role.ADMIN:
        return render(request, "attendance/admin_landing.html", {
            "today_count": Attendance.objects.filter(work_date=timezone.localdate()).count(),
            "leave_pending_count": LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING).count(),
        })
    office = request.user.office
    today = local_now(office).date() if office else timezone.localdate()
    attendance = Attendance.objects.filter(employee=request.user, work_date=today).prefetch_related("events").first()
    events = {event.kind: event for event in attendance.events.all()} if attendance else {}
    try:
        face_status = "active" if request.user.face_profile else "missing"
    except FaceProfile.DoesNotExist:
        face_status = "missing"

    # Cek apakah ada izin/sakit yang aktif hari ini
    active_leave = LeaveRequest.objects.filter(
        employee=request.user,
        status=LeaveRequest.Status.APPROVED,
        start_date__lte=today,
        end_date__gte=today,
    ).first()

    return render(request, "attendance/dashboard.html", {
        "attendance": attendance,
        "events": events,
        "face_status": face_status,
        "today": today,
        "active_leave": active_leave,
    })


@login_required
def leave_request_view(request):
    if request.method == "POST":
        form = LeaveRequestForm(request.POST, request.FILES)
        if form.is_valid():
            leave = form.save(commit=False)
            leave.employee = request.user
            leave.save()
            messages.success(request, "Pengajuan izin/sakit berhasil dikirim dan menunggu persetujuan admin.")
            return redirect("dashboard")
    else:
        form = LeaveRequestForm()

    my_leaves = LeaveRequest.objects.filter(employee=request.user)[:20]
    return render(request, "attendance/leave_request.html", {"form": form, "my_leaves": my_leaves})


@login_required
def enrollment(request):
    latest = request.user.enrollments.first()
    return render(request, "attendance/enrollment.html", {"latest": latest, "profile_active": hasattr(request.user, "face_profile")})


@login_required
def history(request):
    rows = Attendance.objects.filter(employee=request.user).prefetch_related("events")[:90]
    return render(request, "attendance/history.html", {"rows": rows})


@require_POST
@login_required
@transaction.atomic
def enrollment_api(request):
    try:
        if request.POST.get("consent") != "true":
            raise FaceError("CONSENT_REQUIRED", "Persetujuan pemrosesan biometrik wajib diberikan.")
        office = request.user.office
        if not office or not office.is_active:
            raise AttendanceError("OFFICE_REQUIRED", "Outlet akun belum aktif.")
        frames = [item.read() for item in request.FILES.getlist("frames")]
        template, _ = enrollment_template(frames)
        FaceEnrollment.objects.filter(employee=request.user, status=FaceEnrollment.Status.PENDING).update(status=FaceEnrollment.Status.REJECTED, rejection_reason="Digantikan pengajuan baru")
        encrypted = seal(template)
        FaceProfile.objects.update_or_create(employee=request.user, defaults={"encrypted_embedding": encrypted})
        enrollment = FaceEnrollment.objects.create(
            employee=request.user, candidate_embedding=b"", status=FaceEnrollment.Status.APPROVED,
            reviewed_at=timezone.now(), rejection_reason="",
        )
        AuditEvent.objects.create(
            actor=request.user, action="face.auto_activate", object_type="FaceEnrollment", object_id=str(enrollment.pk),
            reason="Aktivasi otomatis setelah pendaftaran mandiri", after={"office_id": office.pk, "office": office.name},
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        return JsonResponse({"ok": True, "message": f"Profil wajah aktif di {office.name}.", "enrollment_id": enrollment.pk})
    except (AttendanceError, FaceError) as exc:
        return _error(exc)


@require_POST
@login_required
def challenge_api(request):
    try:
        latitude, longitude, accuracy = _location(request)
        office, distance = detect_office(latitude, longitude, accuracy)
        challenge = make_challenge(request.user)
        return JsonResponse({
            "ok": True, "challenge_id": str(challenge.pk), "actions": challenge.actions, "expires_in": 90,
            "office": {"id": office.pk, "name": office.name, "distance_m": round(distance)},
        })
    except AttendanceError as exc:
        return _error(exc)


@require_POST
@login_required
def attendance_api(request):
    try:
        kind = request.POST.get("event_type")
        if kind not in AttendanceEvent.Kind.values:
            raise AttendanceError("INVALID_EVENT", "Jenis absensi tidak valid.")
        try:
            request_id = uuid.UUID(request.POST.get("request_id", ""))
        except ValueError:
            raise AttendanceError("INVALID_REQUEST", "ID permintaan tidak valid.")
        latitude, longitude, accuracy = _location(request)
        frames = [item.read() for item in request.FILES.getlist("frames")]
        event = record_event(request.user, request_id, request.POST.get("challenge_id"), kind, latitude, longitude, accuracy, frames)
        return JsonResponse({
            "ok": True,
            "message": "Absen masuk berhasil." if kind == AttendanceEvent.Kind.CHECK_IN else "Absen pulang berhasil.",
            "event": {"id": event.pk, "time": timezone.localtime(event.occurred_at).strftime("%H:%M"), "result": event.get_result_display(), "office": event.attendance.office.name},
        })
    except (AttendanceError, FaceError) as exc:
        return _error(exc)


@login_required
def evidence(request, event_id):
    event = get_object_or_404(AttendanceEvent.objects.select_related("attendance"), pk=event_id)
    if event.attendance.employee_id != request.user.id and not request.user.is_staff:
        return JsonResponse({"ok": False, "message": "Akses ditolak."}, status=403)
    path = settings.EVIDENCE_DIR / event.evidence_path
    if not event.evidence_path or not path.exists():
        return JsonResponse({"ok": False, "message": "Foto bukti sudah tidak tersedia."}, status=404)
    from io import BytesIO
    response = FileResponse(BytesIO(open_sealed(path.read_bytes())), content_type="image/jpeg")
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
def enrollment_preview(request, enrollment_id):
    if not request.user.is_staff:
        return JsonResponse({"ok": False, "message": "Akses ditolak."}, status=403)
    enrollment = get_object_or_404(FaceEnrollment, pk=enrollment_id)
    path = settings.EVIDENCE_DIR / enrollment.preview_path
    if not enrollment.preview_path or not path.exists():
        return JsonResponse({"ok": False, "message": "Foto pendaftaran sudah dihapus."}, status=404)
    from io import BytesIO
    response = FileResponse(BytesIO(open_sealed(path.read_bytes())), content_type="image/jpeg")
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
def leave_attachment(request, file_path):
    leave = get_object_or_404(LeaveRequest, attachment=file_path)
    if leave.employee_id != request.user.id and not request.user.is_staff:
        return JsonResponse({"ok": False, "message": "Akses ditolak."}, status=403)
    if not default_storage.exists(file_path):
        return JsonResponse({"ok": False, "message": "Lampiran tidak tersedia."}, status=404)
    response = FileResponse(default_storage.open(file_path, "rb"), as_attachment=True, filename=Path(file_path).name)
    response["Cache-Control"] = "private, no-store"
    return response


@require_GET
def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "unhealthy"}, status=503)

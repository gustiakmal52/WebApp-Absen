import csv

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import format_html

from .forms import EmployeeChangeForm, EmployeeCreationForm
from .models import AuditEvent, Attendance, AttendanceEvent, Employee, FaceEnrollment, FaceProfile, Holiday, LeaveRequest, Office


admin.site.site_header = "Hadir · Ruang Admin"
admin.site.site_title = "Hadir"
admin.site.index_title = "Operasional hari ini"


def _csv_cell(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


@admin.register(Office)
class OfficeAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "start_time", "end_time", "radius_m", "is_active")
    list_filter = ("is_active", "timezone")
    search_fields = ("name", "address")


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ("date", "name", "office")
    list_filter = ("office",)
    date_hierarchy = "date"


@admin.register(Employee)
class EmployeeAdmin(UserAdmin):
    add_form = EmployeeCreationForm
    form = EmployeeChangeForm
    list_display = ("username", "full_name", "office", "role", "work_status")
    list_filter = ("role", "office", "is_active", "resigned_at")
    search_fields = ("username", "full_name")
    ordering = ("full_name",)
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Profil kerja", {"fields": ("full_name", "office", "role", "must_change_password", "resigned_at")}),
        ("Akses", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Waktu", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("username", "full_name", "office", "role", "password1", "password2")}),)
    actions = ("mark_resigned", "reactivate")

    @admin.display(description="Status kerja", ordering="is_active")
    def work_status(self, obj):
        color = "#a43c2d" if obj.resigned_at else ("#467d31" if obj.is_active else "#7a7d76")
        return format_html('<strong style="color:{}">{}</strong>', color, obj.employment_status)

    @admin.action(description="Nonaktifkan karena resign")
    def mark_resigned(self, request, queryset):
        employees = queryset.filter(role=Employee.Role.EMPLOYEE, is_active=True)
        count = 0
        for employee in employees:
            before = {"is_active": employee.is_active, "resigned_at": None}
            employee.is_active = False
            employee.resigned_at = timezone.now()
            employee.save(update_fields=["is_active", "resigned_at"])
            FaceProfile.objects.filter(employee=employee).delete()
            FaceEnrollment.objects.filter(employee=employee).update(candidate_embedding=b"")
            for enrollment in employee.enrollments.filter(status=FaceEnrollment.Status.PENDING):
                _delete_preview(enrollment)
                enrollment.status = FaceEnrollment.Status.REJECTED
                enrollment.rejection_reason = "Akun dinonaktifkan karena resign"
                enrollment.candidate_embedding = b""
                enrollment.save(update_fields=["status", "rejection_reason", "candidate_embedding", "preview_path"])
            AuditEvent.objects.create(
                actor=request.user, action="employee.resign", object_type="Employee", object_id=str(employee.pk),
                reason="Karyawan dinonaktifkan karena resign", before=before,
                after={"is_active": False, "resigned_at": employee.resigned_at.isoformat()},
                ip_address=request.META.get("REMOTE_ADDR"),
            )
            count += 1
        self.message_user(request, f"{count} karyawan dinonaktifkan. Riwayat absensi tetap tersimpan.", messages.SUCCESS)

    @admin.action(description="Aktifkan kembali karyawan")
    def reactivate(self, request, queryset):
        employees = queryset.filter(role=Employee.Role.EMPLOYEE, is_active=False)
        count = 0
        for employee in employees:
            before = {"is_active": employee.is_active, "resigned_at": employee.resigned_at.isoformat() if employee.resigned_at else None}
            employee.is_active = True
            employee.resigned_at = None
            employee.save(update_fields=["is_active", "resigned_at"])
            AuditEvent.objects.create(
                actor=request.user, action="employee.reactivate", object_type="Employee", object_id=str(employee.pk),
                reason="Karyawan diaktifkan kembali", before=before,
                after={"is_active": True, "resigned_at": None}, ip_address=request.META.get("REMOTE_ADDR"),
            )
            count += 1
        self.message_user(request, f"{count} karyawan diaktifkan kembali. Profil wajah harus didaftarkan ulang.", messages.SUCCESS)


def _delete_preview(enrollment):
    if enrollment.preview_path:
        (settings.EVIDENCE_DIR / enrollment.preview_path).unlink(missing_ok=True)
        enrollment.preview_path = ""


@admin.register(FaceEnrollment)
class FaceEnrollmentAdmin(admin.ModelAdmin):
    list_display = ("employee", "status_badge", "submitted_at", "reviewed_by", "preview_link")
    list_filter = ("status", "submitted_at")
    search_fields = ("employee__full_name", "employee__username")
    readonly_fields = ("employee", "status", "candidate_embedding", "preview_link", "consent_version", "submitted_at", "reviewed_at", "reviewed_by", "rejection_reason")

    def has_add_permission(self, request):
        return False

    @admin.display(description="Status")
    def status_badge(self, obj):
        return obj.get_status_display()

    @admin.display(description="Pratinjau")
    def preview_link(self, obj):
        if obj.preview_path:
            return format_html('<a class="button" href="/enrollment-preview/{}/" target="_blank">Buka foto</a>', obj.pk)
        return "Foto sudah dihapus"

class AttendanceEventInline(admin.TabularInline):
    model = AttendanceEvent
    extra = 0
    fields = ("kind", "occurred_at", "result", "distance_m", "face_score")
    readonly_fields = fields
    can_delete = False


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("work_date", "employee", "office", "status", "check_in", "check_out")
    list_filter = ("status", "office", "work_date")
    search_fields = ("employee__full_name", "employee__username")
    date_hierarchy = "work_date"
    inlines = (AttendanceEventInline,)
    actions = ("export_csv",)
    readonly_fields = tuple(field.name for field in Attendance._meta.fields)

    def has_add_permission(self, request):
        return False

    def check_in(self, obj):
        event = obj.events.filter(kind=AttendanceEvent.Kind.CHECK_IN).first()
        return timezone.localtime(event.occurred_at).strftime("%H:%M") if event else "—"

    def check_out(self, obj):
        event = obj.events.filter(kind=AttendanceEvent.Kind.CHECK_OUT).first()
        return timezone.localtime(event.occurred_at).strftime("%H:%M") if event else "—"

    @admin.action(description="Ekspor CSV terpilih")
    def export_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="absensi.csv"'
        writer = csv.writer(response)
        writer.writerow(["Tanggal", "ID", "Nama", "Kantor", "Masuk", "Status masuk", "Pulang", "Status pulang"])
        for day in queryset.prefetch_related("events").select_related("employee", "office"):
            events = {e.kind: e for e in day.events.all()}
            cin, cout = events.get("CHECK_IN"), events.get("CHECK_OUT")
            writer.writerow([day.work_date, _csv_cell(day.employee.username), _csv_cell(day.employee.full_name), _csv_cell(day.office.name),
                             cin.occurred_at.isoformat() if cin else "", cin.get_result_display() if cin else "",
                             cout.occurred_at.isoformat() if cout else "", cout.get_result_display() if cout else ""])
        return response


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "object_type", "object_id", "reason")
    list_filter = ("action", "object_type")
    readonly_fields = [field.name for field in AuditEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ("employee", "kind", "start_date", "end_date", "status", "submitted_at", "reviewed_by")
    list_filter = ("status", "kind", "start_date")
    search_fields = ("employee__full_name", "employee__username", "reason")
    readonly_fields = ("employee", "kind", "start_date", "end_date", "reason", "attachment", "submitted_at", "reviewed_at", "reviewed_by")
    actions = ("approve_leaves", "reject_leaves")

    @admin.action(description="Setujui pengajuan izin terpilih")
    def approve_leaves(self, request, queryset):
        count = 0
        for leave in queryset.filter(status=LeaveRequest.Status.PENDING):
            leave.status = LeaveRequest.Status.APPROVED
            leave.reviewed_at = timezone.now()
            leave.reviewed_by = request.user
            leave.save(update_fields=["status", "reviewed_at", "reviewed_by"])
            AuditEvent.objects.create(
                actor=request.user, action="leave.approve", object_type="LeaveRequest", object_id=str(leave.pk),
                reason="Pengajuan izin/cuti disetujui",
                after={"status": LeaveRequest.Status.APPROVED},
                ip_address=request.META.get("REMOTE_ADDR")
            )
            count += 1
        self.message_user(request, f"{count} pengajuan izin/cuti berhasil disetujui.", messages.SUCCESS)

    @admin.action(description="Tolak pengajuan izin terpilih")
    def reject_leaves(self, request, queryset):
        count = 0
        for leave in queryset.filter(status=LeaveRequest.Status.PENDING):
            leave.status = LeaveRequest.Status.REJECTED
            leave.reviewed_at = timezone.now()
            leave.reviewed_by = request.user
            leave.save(update_fields=["status", "reviewed_at", "reviewed_by"])
            AuditEvent.objects.create(
                actor=request.user, action="leave.reject", object_type="LeaveRequest", object_id=str(leave.pk),
                reason="Pengajuan izin/cuti ditolak",
                after={"status": LeaveRequest.Status.REJECTED},
                ip_address=request.META.get("REMOTE_ADDR")
            )
            count += 1
        self.message_user(request, f"{count} pengajuan izin/cuti ditolak.", messages.WARNING)

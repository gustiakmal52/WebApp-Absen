import uuid
from datetime import time
from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def default_workdays():
    return [0, 1, 2, 3, 4]


class Office(models.Model):
    name = models.CharField("Nama kantor", max_length=120)
    address = models.TextField("Alamat")
    latitude = models.DecimalField(max_digits=10, decimal_places=7)
    longitude = models.DecimalField(max_digits=10, decimal_places=7)
    radius_m = models.PositiveIntegerField("Radius (meter)", default=150, validators=[MinValueValidator(20), MaxValueValidator(5000)])
    max_accuracy_m = models.PositiveIntegerField("Akurasi GPS maksimum", default=75, validators=[MinValueValidator(10), MaxValueValidator(1000)])
    timezone = models.CharField(default="Asia/Makassar", max_length=64)
    workdays = models.JSONField("Hari kerja", default=default_workdays)
    start_time = models.TimeField("Jam masuk", default=time(8, 0))
    end_time = models.TimeField("Jam pulang", default=time(17, 0))
    grace_minutes = models.PositiveSmallIntegerField("Toleransi terlambat", default=10, validators=[MaxValueValidator(180)])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Kantor"
        verbose_name_plural = "Kantor"

    def __str__(self):
        return self.name


class Employee(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        EMPLOYEE = "EMPLOYEE", "Karyawan"

    full_name = models.CharField("Nama lengkap", max_length=160)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.EMPLOYEE)
    office = models.ForeignKey(Office, on_delete=models.PROTECT, null=True, blank=True, related_name="employees")
    must_change_password = models.BooleanField(default=True)
    resigned_at = models.DateTimeField("Tanggal resign", null=True, blank=True)

    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "Karyawan"
        verbose_name_plural = "Karyawan"

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.role = self.Role.ADMIN
        self.is_staff = self.role == self.Role.ADMIN or self.is_superuser
        super().save(*args, **kwargs)

    def get_full_name(self):
        return self.full_name

    def __str__(self):
        return f"{self.full_name} · {self.username}"

    @property
    def employment_status(self):
        return "Resign" if self.resigned_at else ("Aktif" if self.is_active else "Nonaktif")


class Holiday(models.Model):
    office = models.ForeignKey(Office, on_delete=models.CASCADE, related_name="holidays")
    date = models.DateField("Tanggal")
    name = models.CharField("Keterangan", max_length=120)

    class Meta:
        ordering = ["-date"]
        constraints = [models.UniqueConstraint(fields=["office", "date"], name="unique_office_holiday")]
        verbose_name = "Hari libur"
        verbose_name_plural = "Hari libur"

    def __str__(self):
        return f"{self.date:%d-%m-%Y} · {self.name}"


class FaceEnrollment(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Menunggu"
        APPROVED = "APPROVED", "Disetujui"
        REJECTED = "REJECTED", "Ditolak"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="enrollments")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    candidate_embedding = models.BinaryField(editable=False)
    preview_path = models.CharField(max_length=255, blank=True, editable=False)
    consent_version = models.CharField(max_length=32, default="2026-09")
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_enrollments")
    rejection_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name = "Riwayat profil wajah"
        verbose_name_plural = "Riwayat profil wajah"

    def __str__(self):
        return f"{self.employee.full_name} · {self.get_status_display()}"


class FaceProfile(models.Model):
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name="face_profile")
    encrypted_embedding = models.BinaryField(editable=False)
    model_version = models.CharField(max_length=64, default="opencv-sface-2021dec")
    activated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wajah · {self.employee.full_name}"


class LivenessChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="challenges")
    actions = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class Attendance(models.Model):
    class Status(models.TextChoices):
        INCOMPLETE = "INCOMPLETE", "Belum lengkap"
        COMPLETE = "COMPLETE", "Selesai"
        CORRECTED = "CORRECTED", "Dikoreksi"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="attendance_days")
    office = models.ForeignKey(Office, on_delete=models.PROTECT, related_name="attendance_days")
    work_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.INCOMPLETE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-work_date", "employee__full_name"]
        constraints = [models.UniqueConstraint(fields=["employee", "work_date"], name="unique_employee_workday")]
        verbose_name = "Absensi"
        verbose_name_plural = "Absensi"

    def __str__(self):
        return f"{self.employee.full_name} · {self.work_date:%d-%m-%Y}"


class AttendanceEvent(models.Model):
    class Kind(models.TextChoices):
        CHECK_IN = "CHECK_IN", "Masuk"
        CHECK_OUT = "CHECK_OUT", "Pulang"

    class Result(models.TextChoices):
        ON_TIME = "ON_TIME", "Tepat waktu"
        LATE = "LATE", "Terlambat"
        EARLY = "EARLY", "Pulang awal"
        COMPLETE = "COMPLETE", "Selesai"
        MANUAL = "MANUAL", "Koreksi admin"

    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, related_name="events")
    request_id = models.UUIDField(default=uuid.uuid4, unique=True)
    challenge = models.OneToOneField(LivenessChallenge, on_delete=models.SET_NULL, null=True, blank=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    occurred_at = models.DateTimeField()
    result = models.CharField(max_length=16, choices=Result.choices)
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    accuracy_m = models.FloatField(null=True, blank=True)
    distance_m = models.FloatField(null=True, blank=True)
    face_score = models.FloatField(null=True, blank=True)
    evidence_path = models.CharField(max_length=255, blank=True)
    created_by_admin = models.BooleanField(default=False)

    class Meta:
        ordering = ["occurred_at"]
        constraints = [models.UniqueConstraint(fields=["attendance", "kind"], name="unique_attendance_event_kind")]
        verbose_name = "Kejadian absensi"
        verbose_name_plural = "Kejadian absensi"


class AuditEvent(models.Model):
    actor = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, related_name="audit_actions")
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80)
    reason = models.CharField(max_length=500)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Jejak audit"
        verbose_name_plural = "Jejak audit"


class LeaveRequest(models.Model):
    class Kind(models.TextChoices):
        SICK = "SICK", "Sakit"
        PERMISSION = "PERMISSION", "Izin"
        VACATION = "VACATION", "Cuti"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Menunggu"
        APPROVED = "APPROVED", "Disetujui"
        REJECTED = "REJECTED", "Ditolak"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="leave_requests")
    kind = models.CharField("Jenis pengajuan", max_length=16, choices=Kind.choices, default=Kind.PERMISSION)
    start_date = models.DateField("Tanggal mulai")
    end_date = models.DateField("Tanggal selesai")
    reason = models.TextField("Alasan / Keterangan")
    attachment = models.FileField("Lampiran surat / bukti", upload_to="leaves/%Y/%m/", null=True, blank=True)
    status = models.CharField("Status", max_length=16, choices=Status.choices, default=Status.PENDING)
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_leaves")
    review_notes = models.CharField("Catatan peninjau", max_length=300, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name = "Pengajuan izin / cuti"
        verbose_name_plural = "Pengajuan izin / cuti"

    def __str__(self):
        return f"{self.employee.full_name} · {self.get_kind_display()} ({self.start_date:%d/%m/%Y} - {self.end_date:%d/%m/%Y})"

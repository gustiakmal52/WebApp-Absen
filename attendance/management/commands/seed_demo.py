import os
from django.core.management.base import BaseCommand, CommandError
from attendance.models import Employee


class Command(BaseCommand):
    help = "Buat akun demo tanpa data kantor; atur kantor sendiri sebelum absensi"

    def handle(self, *args, **options):
        password = os.getenv("DEMO_PASSWORD")
        if not password or len(password) < 10:
            raise CommandError("Set DEMO_PASSWORD minimal 10 karakter.")
        admin, _ = Employee.objects.get_or_create(username="admin", defaults={"full_name": "Admin Hadir", "role": Employee.Role.ADMIN, "is_superuser": True, "is_staff": True})
        admin.set_password(password)
        admin.must_change_password = False
        admin.save()
        employee, _ = Employee.objects.get_or_create(username="KRY-001", defaults={"full_name": "Karyawan Demo"})
        employee.set_password(password)
        employee.must_change_password = False
        employee.save()
        self.stdout.write(self.style.SUCCESS("Akun demo siap. Tambahkan kantor dan tetapkan ke KRY-001 sebelum absensi."))

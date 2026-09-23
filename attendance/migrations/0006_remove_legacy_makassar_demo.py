from django.db import migrations


def remove_legacy_makassar_demo(apps, schema_editor):
    Office = apps.get_model("attendance", "Office")
    Attendance = apps.get_model("attendance", "Attendance")
    Employee = apps.get_model("attendance", "Employee")

    legacy = Office.objects.filter(name="Kantor Utama", address__icontains="Makassar")
    central = Office.objects.filter(name="Kantor Pusat").first()
    if central:
        Employee.objects.filter(office__in=legacy).update(office=central)
    Attendance.objects.filter(office__in=legacy).delete()
    legacy.delete()


class Migration(migrations.Migration):
    dependencies = [("attendance", "0005_simplify_automatic_attendance")]
    operations = [migrations.RunPython(remove_legacy_makassar_demo, migrations.RunPython.noop)]

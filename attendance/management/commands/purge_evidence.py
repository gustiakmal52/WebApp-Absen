from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from attendance.models import AttendanceEvent, FaceEnrollment


class Command(BaseCommand):
    help = "Hapus foto bukti yang melewati masa retensi"

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=30)
        deleted = 0
        for event in AttendanceEvent.objects.filter(occurred_at__lt=cutoff).exclude(evidence_path=""):
            (settings.EVIDENCE_DIR / event.evidence_path).unlink(missing_ok=True)
            event.evidence_path = ""
            event.save(update_fields=["evidence_path"])
            deleted += 1
        stale = FaceEnrollment.objects.filter(status=FaceEnrollment.Status.PENDING, submitted_at__lt=timezone.now() - timedelta(days=7))
        for enrollment in stale:
            if enrollment.preview_path:
                (settings.EVIDENCE_DIR / enrollment.preview_path).unlink(missing_ok=True)
            enrollment.preview_path = ""
            enrollment.candidate_embedding = b""
            enrollment.status = FaceEnrollment.Status.REJECTED
            enrollment.rejection_reason = "Pengajuan kedaluwarsa"
            enrollment.save(update_fields=["preview_path", "candidate_embedding", "status", "rejection_reason"])
        FaceEnrollment.objects.filter(status=FaceEnrollment.Status.APPROVED).exclude(candidate_embedding=b"").update(candidate_embedding=b"")
        self.stdout.write(self.style.SUCCESS(f"{deleted} foto bukti dihapus."))

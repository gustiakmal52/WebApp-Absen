from django.urls import path
from . import views


urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("register/", views.register, name="register"),
    path("enrollment/", views.enrollment, name="enrollment"),
    path("history/", views.history, name="history"),
    path("leave/", views.leave_request_view, name="leave_request"),
    path("evidence/<int:event_id>/", views.evidence, name="evidence"),
    path("enrollment-preview/<int:enrollment_id>/", views.enrollment_preview, name="enrollment_preview"),
    path("protected-media/<path:file_path>", views.leave_attachment, name="leave_attachment"),
    path("api/enrollment/", views.enrollment_api, name="enrollment_api"),
    path("api/challenge/", views.challenge_api, name="challenge_api"),
    path("api/attendance/", views.attendance_api, name="attendance_api"),
    path("healthz", views.healthz, name="healthz"),
]

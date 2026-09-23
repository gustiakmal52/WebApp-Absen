from pathlib import Path

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserChangeForm, UserCreationForm
from django.core.exceptions import ValidationError
from .models import Employee, LeaveRequest, Office


class LoginForm(AuthenticationForm):
    username = forms.CharField(label="ID karyawan", widget=forms.TextInput(attrs={"autocomplete": "username", "placeholder": "Contoh: KRY-001", "autofocus": True}))
    password = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "placeholder": "Masukkan password"}))

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        portal = "employee"
        if request:
            portal = request.POST.get("portal") or request.GET.get("portal", "employee")
        if portal == "admin":
            self.fields["username"].label = "Username admin"
            self.fields["username"].widget.attrs["placeholder"] = "Contoh: admin"

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        portal = self.request.POST.get("portal") or self.request.GET.get("portal", "employee")
        if portal == "admin" and user.role != Employee.Role.ADMIN:
            raise ValidationError("Gunakan portal karyawan untuk akun ini.", code="invalid_portal")
        if portal != "admin" and user.role == Employee.Role.ADMIN:
            raise ValidationError("Gunakan portal admin untuk akun ini.", code="invalid_portal")


class EmployeeRegistrationForm(UserCreationForm):
    full_name = forms.CharField(label="Nama lengkap", max_length=160)
    office = forms.ModelChoiceField(label="Outlet", queryset=Office.objects.filter(is_active=True), empty_label="Pilih outlet tempat bekerja")

    class Meta:
        model = Employee
        fields = ("username", "full_name", "office", "password1", "password2")
        labels = {"username": "ID karyawan"}
        widgets = {"username": forms.TextInput(attrs={"autocomplete": "username", "placeholder": "Contoh: KRY-001", "autofocus": True})}


class EmployeeCreationForm(UserCreationForm):
    class Meta:
        model = Employee
        fields = ("username", "full_name", "office", "role")


class EmployeeChangeForm(UserChangeForm):
    class Meta:
        model = Employee
        fields = "__all__"


class LeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ("kind", "start_date", "end_date", "reason", "attachment")
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "reason": forms.Textarea(attrs={"rows": 3, "placeholder": "Tuliskan alasan pengajuan..."}),
        }

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get("start_date")
        end = cleaned_data.get("end_date")
        if start and end and start > end:
            self.add_error("end_date", "Tanggal selesai tidak boleh sebelum tanggal mulai.")
        return cleaned_data

    def clean_attachment(self):
        attachment = self.cleaned_data.get("attachment")
        if not attachment:
            return attachment
        if attachment.size > 5 * 1024 * 1024:
            raise forms.ValidationError("Ukuran lampiran maksimal 5 MB.")
        if Path(attachment.name).suffix.lower() not in {".pdf", ".jpg", ".jpeg", ".png"}:
            raise forms.ValidationError("Lampiran harus berupa PDF, JPG, atau PNG.")
        return attachment

# Hadir

Aplikasi Django 5.2 untuk absensi karyawan dengan GPS, verifikasi wajah, izin/cuti, dan panel admin. Model wajah memakai OpenCV YuNet + SFace. Kamera dan GPS browser memerlukan `localhost` atau HTTPS.

Kode aplikasi tersedia dengan [lisensi MIT](LICENSE). Setiap pengelola perlu menyiapkan kantor, akun, dan kebijakan data biometriknya sendiri.

Model wajah perlu diunduh seperti langkah berikut. Database, akun, bukti foto, lampiran pribadi, dan secret tidak disertakan di repository.

## Lokal

Jalankan dari direktori proyek, dengan Python 3.10+:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p var/models var/evidence var/uploads
curl -fL -o var/models/face_detection_yunet_2023mar.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
curl -fL -o var/models/face_recognition_sface_2021dec.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx
.venv/bin/python manage.py migrate
DEMO_PASSWORD='ganti-dengan-password-acak-panjang' .venv/bin/python manage.py seed_demo
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

Buka `http://127.0.0.1:8000`. Akun demo `admin` (portal admin) dan `KRY-001` (portal karyawan) memakai `DEMO_PASSWORD`. `seed_demo` tidak membuat kantor; tambahkan kantor melalui admin dan tetapkan ke akun demo sebelum menguji absensi. Jalankan `seed_demo` hanya untuk data uji: perintah itu mengatur ulang password kedua akun tersebut. Jangan gunakan untuk produksi.

Untuk menjalankan lagi setelah instalasi awal, cukup:

```bash
cd /path/ke/hadir
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

Jika menguji dari HP, jalankan tunnel HTTPS pada **terminal lain**:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

Ambil hostname tunnel yang ditampilkan, isi `ALLOWED_HOSTS` dengan hostname tersebut dan `CSRF_TRUSTED_ORIGINS` dengan origin HTTPS-nya, lalu mulai ulang server lokal. Jangan gunakan wildcard Host atau origin. Akses lewat tunnel hanya untuk pengujian; langkah deploy ada di bawah.

## Menambah outlet

Instalasi baru belum memiliki outlet. Tambahkan masing-masing secara manual; outlet tidak ditemukan otomatis dari GPS:

1. Masuk lewat portal admin, lalu buka `/admin/attendance/office/add/` (menu **Kantor → Tambah kantor**).
2. Isi nama dan alamat outlet. Ambil **latitude** dan **longitude** dari titik lokasi absensi yang sebenarnya, lalu atur radius dan batas akurasi GPS sesuai kondisi outlet.
3. Periksa zona waktu, hari kerja, jam masuk/pulang, dan toleransi keterlambatan. Nilai awal zona waktu adalah `Asia/Makassar`; ubah sesuai lokasi outlet.
4. Centang **Is active/Aktif** dan simpan. Outlet aktif baru muncul dalam pilihan outlet saat pendaftaran karyawan dan ikut diperiksa saat absensi.
5. Jika pendaftaran mandiri dimatikan di produksi, buat atau ubah akun karyawan melalui menu **Karyawan** dan pilih outletnya pada kolom **Office/Kantor**.

Cek daftar outlet yang tersimpan:

```bash
.venv/bin/python manage.py shell -c 'from attendance.models import Office; print(list(Office.objects.values("name", "is_active")))'
```

Saat absensi, aplikasi memilih outlet **aktif terdekat** dari koordinat GPS dan menolak lokasi di luar radius. Saat ini pilihan itu belum dibatasi ke outlet yang tercatat pada profil karyawan; penugasan karyawan ke outlet tertentu belum menjadi aturan pembatas absensi.

## Panduan admin setelah aplikasi di-deploy

1. Admin pertama dibuat oleh orang yang men-deploy aplikasi dengan `.venv/bin/python manage.py createsuperuser` (lihat bagian deploy). Buka `https://domain-anda/login/?portal=admin`, masuk dengan akun tersebut, lalu pilih **Panel Admin** atau buka `/admin/`. Jangan memakai akun dari `seed_demo` untuk produksi.
2. Di **Kantor**, masukkan semua outlet organisasi dan jadwalnya sesuai bagian *Menambah outlet* di atas. Tambahkan hari libur per outlet lewat menu **Hari libur** bila diperlukan. Periksa titik GPS dan radius di lokasi nyata sebelum karyawan mulai absen.
3. Di **Karyawan**, buat akun karyawan, tentukan outlet pada kolom **Office/Kantor**, dan berikan password awal yang unik. Pengguna biasa memakai role `EMPLOYEE`; jangan berikan `is_superuser` atau izin admin kepada karyawan. Akun dengan role `ADMIN` selain superuser awal perlu diberi permission Django yang sesuai agar dapat mengelola menu admin.
4. Karyawan masuk melalui portal **Karyawan**, lalu merekam wajah dengan persetujuan biometrik. Profil wajah yang lolos pemeriksaan aplikasi langsung aktif; **tidak ada langkah persetujuan wajah oleh admin**. Menu **Riwayat profil wajah** hanya untuk melihat riwayat.
5. Di **Pengajuan izin / cuti**, buka pengajuan dan lampirannya jika ada. Pilih pengajuan berstatus *Menunggu*, lalu gunakan aksi **Setujui pengajuan izin terpilih** atau **Tolak pengajuan izin terpilih**. Aksi ini mencatat peninjau dan jejak audit.
6. Di **Absensi**, saring berdasarkan tanggal, outlet, atau status; buka baris untuk melihat waktu masuk/pulang dan rincian kejadian. Pilih baris dan aksi **Ekspor CSV terpilih** untuk mengunduh laporan. Hapus catatan hanya bila memang diperlukan karena penghapusan data tidak dapat dibatalkan dari panel.
7. Bila karyawan berhenti, pilih akunnya di **Karyawan** lalu jalankan aksi **Nonaktifkan karena resign**. Login dinonaktifkan dan profil wajah aktif dihapus, sedangkan riwayat absensi tetap ada. Aksi **Aktifkan kembali karyawan** mengaktifkan login lagi; karyawan perlu mendaftarkan wajah ulang. Perubahan penting dapat dilihat pada menu **Jejak audit**.

Lampiran izin dan foto bukti hanya diakses melalui aplikasi sesuai hak akses; jangan membuka direktori `var/` untuk publik di server. Jalankan `purge_evidence` terjadwal sebagaimana dijelaskan pada bagian deploy.

## Tes

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test
.venv/bin/python -m pip check
```

Tes mencakup alur absensi, GPS, replay ID permintaan, kontrol akses bukti dan lampiran, CSRF, pendaftaran, serta perlindungan ekspor CSV. Pengujian kamera asli, spoofing GPS, dan ketahanan liveness tetap perlu dilakukan pada perangkat dan lingkungan operasional.

## Hasil security testing lokal (23 September 2026)

**Status: pengujian keamanan terarah sudah dijalankan dan lulus untuk kasus di bawah.** Ini bukan sertifikasi keamanan atau pentest menyeluruh.

- `manage.py test`: **31/31 tes lulus**. Tes keamanan memeriksa akses foto bukti dan lampiran oleh akun lain (`403`), penolakan POST tanpa CSRF (`403`), pemisahan portal karyawan/admin, serta penolakan login akun yang resign.
- Replay `request_id` untuk jenis absensi atau akun lain ditolak (`409`). Urutan wajah dengan satu frame yang tidak cocok ditolak pada unit test; ekspor CSV memberi escape pada nama yang menyerupai formula spreadsheet.
- Uji HTTP Gunicorn dengan konfigurasi produksi lokal: `/healthz` dan halaman login `200`, pendaftaran publik `404`, Host asing `400`.
- `manage.py check --deploy` dengan konfigurasi produksi tidak melaporkan error. Dua peringatan yang tersisa adalah HSTS subdomain (`security.W005`) dan preload (`security.W021`), sengaja belum diaktifkan tanpa kepastian HTTPS untuk seluruh subdomain.

**Belum diuji:** server/domain pengguna, browser dan kamera pada HP karyawan, pemalsuan GPS, serta serangan video/deepfake terhadap liveness. Sebelum operasional, pengelola perlu menguji alur absensi nyata di setiap outlet dan konfigurasi HTTPS di server tujuan.

## Deploy VPS (Gunicorn + Caddy + PostgreSQL)

Contoh ini memakai satu VPS Linux dan domain sendiri. Siapkan DNS ke VPS serta buka port 80/443. Pasang Python, PostgreSQL, dan [Caddy sesuai dokumentasi resminya](https://caddyserver.com/docs/install). Buat user layanan dengan `sudo useradd --system --create-home --shell /bin/bash hadir`, tempatkan kode di `/srv/hadir` milik user `hadir`, buat virtualenv, pasang dependensi, lalu unduh kedua model seperti langkah lokal. Direktori `var/models`, `var/evidence`, dan `var/uploads` harus persisten; jangan layani `var/` sebagai file statis publik.

Buat database PostgreSQL melalui `sudo -u postgres psql`, kemudian jalankan SQL berikut dengan password acak yang sama seperti `POSTGRES_PASSWORD`:

```sql
CREATE USER hadir WITH PASSWORD '<password-database>';
CREATE DATABASE hadir OWNER hadir;
```

Simpan konfigurasi pada `/etc/hadir.env`, izin file `0640`, pemilik `root:hadir`. Ganti domain, nama database, pengguna, dan seluruh secret. Buat dua secret acak yang **berbeda**, misalnya dengan `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'` dua kali. Simpan `BIOMETRIC_KEY` dengan aman bersama backup: kehilangan kunci itu membuat profil dan foto terenkripsi lama tidak terbaca.

```dotenv
DEBUG=0
SECRET_KEY=<secret-acak-pertama>
BIOMETRIC_KEY=<secret-acak-kedua>
ALLOWED_HOSTS=hadir.example.com
CSRF_TRUSTED_ORIGINS=https://hadir.example.com
TRUST_PROXY_HTTPS=1
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=hadir
POSTGRES_USER=hadir
POSTGRES_PASSWORD=<password-database>
ALLOW_SELF_REGISTRATION=0
```

`ALLOW_SELF_REGISTRATION=0` menutup pembuatan akun publik; buat admin dengan `createsuperuser`, lalu kelola karyawan dari panel admin. Ubah ke `1` hanya jika pendaftaran mandiri memang diizinkan. Jangan menjalankan `seed_demo` pada database produksi. Variabel `TRUST_PROXY_HTTPS=1` hanya aman jika Gunicorn terikat ke loopback dan Caddy menjadi satu-satunya proxy yang dapat mengaksesnya.

Jalankan persiapan sebagai user layanan:

```bash
sudo -iu hadir
set -a
. /etc/hadir.env
set +a
cd /srv/hadir
.venv/bin/python manage.py check --deploy
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser
exit
```

`check --deploy` akan memberi dua peringatan opsional tentang HSTS subdomain dan preload. Keduanya sengaja tidak aktif karena konfigurasi ini tidak mengasumsikan seluruh subdomain memakai HTTPS atau sudah siap masuk daftar preload.

Contoh unit systemd `/etc/systemd/system/hadir.service`:

```ini
[Unit]
Description=Hadir
After=network.target postgresql.service

[Service]
User=hadir
Group=hadir
WorkingDirectory=/srv/hadir
EnvironmentFile=/etc/hadir.env
ExecStart=/srv/hadir/.venv/bin/gunicorn hadir.wsgi:application --bind 127.0.0.1:8000 --workers 2 --timeout 120
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Contoh Caddyfile `/etc/caddy/Caddyfile` (ganti domain dan path proyek):

```caddyfile
hadir.example.com {
    handle_path /static/* {
        root * /srv/hadir/staticfiles
        file_server
    }
    handle {
        reverse_proxy 127.0.0.1:8000
    }
}
```

Pastikan Caddy bisa membaca `staticfiles`, tetapi **tidak** mendapat akses file langsung ke `var/`. Setelah DNS aktif, jalankan:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hadir
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -f https://hadir.example.com/healthz
sudo journalctl -u hadir --no-pager -n 50
```

Periksa juga halaman login, file CSS, dan login admin. Caddy mengurus sertifikat TLS untuk domain publik jika DNS serta port 80/443 benar.

Jadwalkan `.venv/bin/python manage.py purge_evidence` setiap hari sebagai user layanan. Backup PostgreSQL dan `var/` secara terenkripsi; uji pemulihannya sebelum dipakai operasional. Saat rilis berikutnya: backup, pasang kode/dependensi, jalankan tes dan `check --deploy`, lalu `migrate`, `collectstatic`, restart Gunicorn, dan cek endpoint serta log. Untuk rollback, kembalikan kode dan backup database yang cocok dengan migrasi tersebut.

## Batas keamanan yang perlu diketahui

Radius GPS berasal dari koordinat browser, sehingga pengguna dengan perangkat yang memalsukan lokasi masih bisa melewatinya. Tantangan gerak RGB membantu menolak foto statis, tetapi bukan liveness biometrik tersertifikasi atau perlindungan kuat terhadap video/deepfake. Penerapan untuk keputusan berisiko tinggi memerlukan verifikasi perangkat/lokasi dan liveness tambahan yang divalidasi di lapangan.

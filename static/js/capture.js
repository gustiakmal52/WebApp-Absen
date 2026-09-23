const root = document.querySelector('[data-capture-mode]');
if (!root) throw new Error('capture root missing');

const overlay = document.querySelector('#capture-overlay');
const video = document.querySelector('#camera-video');
const canvas = document.querySelector('#camera-canvas');
const title = document.querySelector('#capture-title');
const message = document.querySelector('#capture-message');
const step = document.querySelector('#capture-step');
const progress = document.querySelector('#capture-progress');
let stream;

const csrf = () => document.cookie.split('; ').find(v => v.startsWith('csrftoken='))?.split('=')[1] || '';
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const getLocation = () => new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(resolve, reject, {enableHighAccuracy: true, timeout: 15000, maximumAge: 0}));

function setState(headline, detail, label = '', percent = 0) {
  title.textContent = headline;
  message.textContent = detail;
  step.textContent = label;
  progress.style.width = `${percent}%`;
}

async function openCamera() {
  overlay.hidden = false;
  document.body.classList.add('capture-open');
  stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: 'user', width: {ideal: 640}, height: {ideal: 480}}, audio: false});
  video.srcObject = stream;
  await video.play();
}

function closeCamera() {
  stream?.getTracks().forEach(track => track.stop());
  stream = null;
  overlay.hidden = true;
  document.body.classList.remove('capture-open');
}

async function snap() {
  canvas.width = 640;
  canvas.height = 480;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(video, 0, 0, 640, 480);
  return new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .78));
}

async function post(url, data) {
  const response = await fetch(url, {method: 'POST', body: data, headers: {'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest'}});
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.message || 'Permintaan gagal.');
  return payload;
}

function fail(error) {
  setState('Belum berhasil', error.message || 'Periksa izin kamera dan coba lagi.', 'COBA SEKALI LAGI', 100);
  overlay.querySelector('.camera-frame').classList.add('error');
}

document.querySelector('#capture-close')?.addEventListener('click', closeCamera);

if (root.dataset.captureMode === 'enrollment') {
  document.querySelector('#start-enrollment')?.addEventListener('click', async () => {
    if (!document.querySelector('#biometric-consent')?.checked) {
      document.querySelector('.consent-box').classList.add('shake');
      return;
    }
    try {
      await openCamera();
      const poses = [
        ['Tatap lurus', 'Posisikan wajah di tengah bingkai.'],
        ['Menoleh ke kiri', 'Gerakkan kepala perlahan ke kiri.'],
        ['Menoleh ke kanan', 'Gerakkan kepala perlahan ke kanan.'],
        ['Sedikit lihat ke atas', 'Angkat dagu sedikit.'],
        ['Sedikit lihat ke bawah', 'Turunkan dagu sedikit.'],
      ];
      const frames = [];
      for (let i = 0; i < poses.length; i++) {
        setState(poses[i][0], poses[i][1], `LANGKAH ${i + 1} DARI 5`, i * 20);
        await wait(1500);
        frames.push(await snap());
        progress.style.width = `${(i + 1) * 20}%`;
      }
      setState('Mengenali wajah…', 'Data sedang dienkripsi dan diperiksa.', 'HAMPIR SELESAI', 100);
      const data = new FormData();
      data.append('consent', 'true');
      frames.forEach((frame, i) => data.append('frames', frame, `face-${i}.jpg`));
      const result = await post('/api/enrollment/', data);
      setState('Profil wajah aktif', result.message, 'SELESAI', 100);
      await wait(1400);
      location.reload();
    } catch (error) { fail(error); }
  });
}

if (root.dataset.captureMode === 'attendance') {
  document.querySelectorAll('[data-start-attendance]').forEach(button => button.addEventListener('click', async () => {
    try {
      overlay.querySelector('.camera-frame').classList.remove('error');
      setState('Memeriksa lokasi…', 'Pastikan izin lokasi aktif.', 'LANGKAH 1 DARI 3', 10);
      overlay.hidden = false;
      document.body.classList.add('capture-open');
      const position = await getLocation();
      const location = new FormData();
      location.append('latitude', position.coords.latitude);
      location.append('longitude', position.coords.longitude);
      location.append('accuracy_m', position.coords.accuracy);
      const challenge = await post('/api/challenge/', location);
      setState(`Outlet: ${challenge.office.name}`, `${challenge.office.distance_m} meter dari titik outlet.`, 'LOKASI TERVERIFIKASI', 20);
      await openCamera();
      const labels = {TURN_LEFT: ['Menoleh ke kiri', 'Ikuti arah dengan perlahan.'], TURN_RIGHT: ['Menoleh ke kanan', 'Ikuti arah dengan perlahan.']};
      const frames = [];
      setState('Tatap lurus', 'Posisikan wajah di tengah bingkai.', 'LANGKAH 2 DARI 3', 30);
      for (let i = 0; i < 5; i++) { frames.push(await snap()); await wait(280); }
      for (let a = 0; a < challenge.actions.length; a++) {
        const copy = labels[challenge.actions[a]];
        setState(copy[0], copy[1], `GERAKAN ${a + 1} DARI 2`, 45 + a * 25);
        for (let i = 0; i < 7; i++) { frames.push(await snap()); await wait(280); }
      }
      setState('Memverifikasi…', 'Wajah, lokasi, dan jadwal sedang diperiksa.', 'LANGKAH 3 DARI 3', 92);
      const data = new FormData();
      data.append('request_id', crypto.randomUUID());
      data.append('challenge_id', challenge.challenge_id);
      data.append('event_type', button.dataset.startAttendance);
      data.append('latitude', position.coords.latitude);
      data.append('longitude', position.coords.longitude);
      data.append('accuracy_m', position.coords.accuracy);
      frames.forEach((frame, i) => data.append('frames', frame, `live-${i}.jpg`));
      const result = await post('/api/attendance/', data);
      setState('Absensi tercatat', `${result.event.time} · ${result.event.office} · ${result.event.result}`, 'BERHASIL', 100);
      await wait(1500);
      location.reload();
    } catch (error) { fail(error); }
  }));
}

const clock = document.querySelector('#live-time');
if (clock) {
  const timeZone = root.dataset.officeTimezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  const formatter = new Intl.DateTimeFormat('id-ID', {hour: '2-digit', minute: '2-digit', hour12: false, timeZone, timeZoneName: 'short'});
  const tick = () => {
    const parts = formatter.formatToParts(new Date());
    clock.textContent = `${parts.find(part => part.type === 'hour').value}:${parts.find(part => part.type === 'minute').value}`;
    document.querySelector('#live-zone').textContent = parts.find(part => part.type === 'timeZoneName').value;
  };
  tick(); setInterval(tick, 1000);
}

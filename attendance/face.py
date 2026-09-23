from functools import lru_cache

import cv2
import numpy as np
from django.conf import settings


class FaceError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@lru_cache(maxsize=1)
def _models():
    detector_path = settings.MODEL_DIR / "face_detection_yunet_2023mar.onnx"
    recognizer_path = settings.MODEL_DIR / "face_recognition_sface_2021dec.onnx"
    if not detector_path.exists() or not recognizer_path.exists():
        raise FaceError("MODEL_UNAVAILABLE", "Model pengenal wajah belum terpasang di server.")
    detector = cv2.FaceDetectorYN.create(str(detector_path), "", (320, 320), 0.9, 0.3, 5000)
    recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")
    return detector, recognizer


def decode_image(raw):
    if len(raw) > 900_000:
        raise FaceError("IMAGE_TOO_LARGE", "Ukuran satu gambar terlalu besar.")
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[0] < 240 or image.shape[1] < 240:
        raise FaceError("INVALID_IMAGE", "Gambar kamera tidak valid atau terlalu kecil.")
    if max(image.shape[:2]) > 1280:
        scale = 1280 / max(image.shape[:2])
        image = cv2.resize(image, None, fx=scale, fy=scale)
    return image


def analyze(raw):
    detector, recognizer = _models()
    image = decode_image(raw)
    detector.setInputSize((image.shape[1], image.shape[0]))
    _, faces = detector.detect(image)
    if faces is None or len(faces) == 0:
        raise FaceError("FACE_NOT_FOUND", "Wajah tidak terlihat jelas.")
    if len(faces) != 1:
        raise FaceError("MULTIPLE_FACES", "Pastikan hanya satu wajah berada di kamera.")
    face = faces[0]
    if min(face[2], face[3]) < 120:
        raise FaceError("FACE_TOO_SMALL", "Dekatkan wajah ke kamera.")
    crop = image[max(0, int(face[1])):int(face[1] + face[3]), max(0, int(face[0])):int(face[0] + face[2])]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if gray.var() < 180 or cv2.Laplacian(gray, cv2.CV_64F).var() < 28:
        raise FaceError("LOW_QUALITY", "Pencahayaan atau ketajaman gambar belum cukup.")
    aligned = recognizer.alignCrop(image, face)
    embedding = recognizer.feature(aligned).flatten().astype(np.float32)
    embedding /= np.linalg.norm(embedding)
    eye_mid = (face[4] + face[6]) / 2
    eye_distance = max(abs(face[4] - face[6]), 1)
    yaw = float((face[8] - eye_mid) / eye_distance)
    # Camera coordinates are viewer-relative; expose directions from the employee's perspective.
    direction = "CENTER" if abs(yaw) < 0.13 else ("LEFT" if yaw > 0 else "RIGHT")
    return embedding, direction, image, face


def enrollment_template(raw_frames):
    if len(raw_frames) != 5:
        raise FaceError("FRAME_COUNT", "Ambil tepat lima foto wajah.")
    analyzed = [analyze(raw) for raw in raw_frames]
    embeddings = [item[0] for item in analyzed]
    _, recognizer = _models()
    similarities = [recognizer.match(embeddings[0], emb, cv2.FaceRecognizerSF_FR_COSINE) for emb in embeddings[1:]]
    if min(similarities) < settings.FACE_MATCH_THRESHOLD:
        raise FaceError("INCONSISTENT_FACE", "Sampel wajah tidak konsisten. Ulangi perekaman.")
    template = np.mean(embeddings, axis=0).astype(np.float32)
    template /= np.linalg.norm(template)
    return template.tobytes(), raw_frames[0]


def verify_sequence(raw_frames, actions, template_bytes):
    if not 8 <= len(raw_frames) <= 24:
        raise FaceError("FRAME_COUNT", "Urutan kamera tidak lengkap.")
    analyzed = [analyze(raw) for raw in raw_frames]
    directions = [item[1] for item in analyzed]
    expected = [action.replace("TURN_", "") for action in actions]
    cursor = 0
    for direction in directions:
        if direction == expected[cursor]:
            cursor += 1
            if cursor == len(expected):
                break
    if cursor != len(expected):
        raise FaceError("LIVENESS_FAILED", "Gerakan tidak sesuai urutan. Coba sekali lagi.")
    template = np.frombuffer(template_bytes, dtype=np.float32)
    _, recognizer = _models()
    scores = [float(recognizer.match(template, item[0], cv2.FaceRecognizerSF_FR_COSINE)) for item in analyzed]
    score = min(scores)
    if score < settings.FACE_MATCH_THRESHOLD:
        raise FaceError("FACE_MISMATCH", "Wajah tidak cocok dengan profil terdaftar.")
    best_index = scores.index(max(scores))
    ok, encoded = cv2.imencode(".jpg", analyzed[best_index][2], [cv2.IMWRITE_JPEG_QUALITY, 72])
    if not ok:
        raise FaceError("INVALID_IMAGE", "Bukti kamera gagal diproses.")
    return score, encoded.tobytes()

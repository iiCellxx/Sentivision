from datetime import datetime
from flask import Flask, Response, render_template, jsonify, send_file, request
import cv2
import threading
import queue
import time
import tempfile
import os
import numpy as np
from ultralytics import YOLO
from deepface import DeepFace
from db import DatabaseHandler
from report import ReportGenerator
from utils import track_id_to_alias

app = Flask(__name__)

# ── Credentials / config from environment (set in .env or shell) ──────────────
# Export these before running:
#   export RTSP_URL="rtsp://user:pass@ip:port/profile0"
# Falls back to a safe placeholder if not set.
_DEFAULT_RTSP = os.environ.get("RTSP_URL", "rtsp://admin:admin123456@192.168.1.15:8554/profile0")

# ── Paths ──────────────────────────────────────────────────────────────────────
MODELS_DIR        = "models"
PERSON_MODEL_PATH = os.path.join(MODELS_DIR, "yolov8n.pt")
FACE_MODEL_PATH   = os.path.join(MODELS_DIR, "fer2013.onnx")

# ── Model validation ─────────────────────────────────────────────────────────
def _validate_models():
    missing = []
    for path in [PERSON_MODEL_PATH, FACE_MODEL_PATH]:
        if not os.path.exists(path):
            missing.append(path)
    if missing:
        raise FileNotFoundError(f"Missing model(s): {', '.join(missing)}")
    print(f"[models] All models validated successfully")

# ── Lazy model loading ───────────────────────────────────────────────────────
_person_model = None
_face_detector = None

def get_person_model():
    global _person_model
    if _person_model is None:
        _person_model = YOLO(PERSON_MODEL_PATH)
    return _person_model

def get_face_detector():
    """Return a thread-local FaceDetectorYN instance."""
    global _face_detector
    if _face_detector is None:
        _face_detector = cv2.FaceDetectorYN.create(FACE_MODEL_PATH, "", (320, 320), 0.9, 0.3, 5000)
    return _face_detector

# ── Database ───────────────────────────────────────────────────────────────────
db = DatabaseHandler()

# ── Queues & events ────────────────────────────────────────────────────────────
frame_queue    = queue.Queue(maxsize=2)
emotion_queue  = queue.Queue(maxsize=8)   # face crops for async DeepFace
stop_event     = threading.Event()

# ── State (all access through state_lock) ─────────────────────────────────────
state_lock        = threading.Lock()
_capturing        = False
_current_session  = None   # session_id
_frame_counter    = 0
_last_min_update  = None
_draining         = False  # True between stop() call and end_session()

# ── Emotion data (published to /emotion_data) ──────────────────────────────────
emotion_lock = threading.Lock()
emotion_data = {'Happy': 0, 'Surprise': 0, 'Sad': 0, 'Fear': 0, 'Angry': 0, 'Disgust': 0}

# ── RTSP URL ───────────────────────────────────────────────────────────────────
rtsp_lock = threading.Lock()
_rtsp_url = _DEFAULT_RTSP

EMOTION_CLASSES = ["angry", "disgust", "fear", "happy", "sad", "surprise", "neutral"]
EMOTION_COLORS  = {
    0: (0, 0, 255), 1: (0, 128, 0),  2: (128, 0, 128),
    3: (0, 255, 255), 4: (255, 0, 0), 5: (0, 165, 255), 6: (128, 128, 128)
}
EMOTION_MAP = {
    'angry': 'Angry', 'disgust': 'Disgust', 'fear': 'Fear',
    'happy': 'Happy', 'sad': 'Sad', 'surprise': 'Surprise'
}


# ── Helpers ────────────────────────────────────────────────────────────────────
def get_rtsp_url():
    with rtsp_lock:
        return _rtsp_url

def set_rtsp_url(new_url):
    global _rtsp_url
    with rtsp_lock:
        _rtsp_url = new_url

def is_capturing():
    with state_lock:
        return _capturing

def get_session():
    with state_lock:
        return _current_session


# ── DeepFace worker (off the main video path) ─────────────────────────────────
# Results keyed by track_id so generate() can pick them up
_emotion_results      = {}
_emotion_results_lock = threading.Lock()

def deepface_worker():
    """
    Consumes (track_id, face_crop) from emotion_queue,
    runs DeepFace.analyze(), stores result in _emotion_results.
    One long-running daemon thread.
    """
    while not stop_event.is_set():
        try:
            track_id, face_crop = emotion_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            result = DeepFace.analyze(
                face_crop, actions=['emotion'],
                enforce_detection=False, silent=True
            )
            if isinstance(result, list):
                result = result[0]
            emotion_name = result['dominant_emotion']
            confidence   = result['emotion'][emotion_name] / 100.0
            with _emotion_results_lock:
                _emotion_results[track_id] = (emotion_name, confidence)
        except Exception as e:
            print(f"[DeepFace worker] {e}")
        finally:
            emotion_queue.task_done()


# ── Frame capture thread ───────────────────────────────────────────────────────
def capture_frames():
    while not stop_event.is_set():
        if not is_capturing():
            time.sleep(0.1)
            continue
        try:
            current_url = get_rtsp_url()
            cap = cv2.VideoCapture(current_url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            cap.set(cv2.CAP_PROP_FPS, 15)
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)

            if not cap.isOpened():
                print("[capture] Failed to open RTSP stream. Retrying in 2 s…")
                time.sleep(2)
                continue

            print("[capture] RTSP stream connected.")
            consecutive_failures = 0

            while not stop_event.is_set() and is_capturing():
                ret, frame = cap.read()
                if not ret:
                    consecutive_failures += 1
                    if consecutive_failures > 10:
                        print("[capture] Too many failures – reconnecting…")
                        break
                    time.sleep(0.1)
                    continue
                consecutive_failures = 0
                # Drop oldest frame if queue is full
                if frame_queue.full():
                    try:
                        frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                frame_queue.put(frame)
                time.sleep(0.03)

            cap.release()
        except Exception as e:
            print(f"[capture] Error: {e}")
            time.sleep(2)


# ── MJPEG generator ────────────────────────────────────────────────────────────
def generate():
    global emotion_data, _frame_counter, _last_min_update

    while True:
        if not is_capturing():
            time.sleep(0.1)
            continue

        try:
            frame = frame_queue.get(timeout=1)
        except queue.Empty:
            continue

        if frame is None or frame.size == 0:
            continue

        try:
            overlay              = frame.copy()
            current_frame_emotions = {'Happy': 0, 'Surprise': 0, 'Sad': 0,
                                       'Fear': 0, 'Angry': 0, 'Disgust': 0}
            detected_count = 0

            session_id = get_session()

            # ── Person detection & tracking ────────────────────────────────────
            person_results = get_person_model().track(
                frame, persist=True, conf=0.5, classes=[0], verbose=False
            )[0]

            if person_results.boxes is not None and person_results.boxes.id is not None:
                track_ids = person_results.boxes.id.int().cpu().tolist()

                for person_box, track_id in zip(person_results.boxes, track_ids):
                    detected_count += 1
                    x1, y1, x2, y2 = map(int, person_box.xyxy[0])

                    # ── Retrieve last known emotion for this track_id ──────────
                    with _emotion_results_lock:
                        emo_result = _emotion_results.get(track_id)

                    color           = EMOTION_COLORS[6]
                    emotion_display = "Neutral"
                    confidence      = 0.0

                    if emo_result:
                        emotion_name, confidence = emo_result
                        emotion_display = emotion_name.capitalize()
                        try:
                            idx   = EMOTION_CLASSES.index(emotion_name)
                            color = EMOTION_COLORS.get(idx, EMOTION_COLORS[6])
                        except ValueError:
                            pass

                        if emotion_name != 'neutral':
                            emotion_key = EMOTION_MAP.get(emotion_name)
                            if emotion_key:
                                current_frame_emotions[emotion_key] += 1

                    # ── Feed face crop to DeepFace worker (non-blocking) ───────
                    person_roi = frame[y1:y2, x1:x2]
                    if person_roi.size > 0:
                        fd = get_face_detector()
                        fd.setInputSize((person_roi.shape[1], person_roi.shape[0]))
                        _, faces = fd.detect(person_roi)
                        if faces is not None and len(faces) > 0:
                            fx, fy, fw, fh = map(int, faces[0][:4])
                            face_crop = person_roi[fy:fy + fh, fx:fx + fw]
                            if face_crop.size > 0 and not emotion_queue.full():
                                emotion_queue.put_nowait((track_id, face_crop.copy()))

                    # ── Draw overlay ───────────────────────────────────────────
                    cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 4)
                    alias      = track_id_to_alias(track_id)
                    label      = f"Person {alias}: {emotion_display} ({confidence:.2f})"
                    label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0]
                    label_y    = max(y1 - 10, label_size[1] + 10)
                    cv2.rectangle(overlay,
                                  (x1, label_y - label_size[1] - 10),
                                  (x1 + label_size[0] + 10, label_y), color, -1)
                    cv2.putText(overlay, label, (x1 + 5, label_y - 5),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            # ── Publish emotion data for /emotion_data ─────────────────────────
            with emotion_lock:
                emotion_data = current_frame_emotions.copy()

            # ── DB writes (every 10 frames) ────────────────────────────────────
            with state_lock:
                _frame_counter += 1
                fc  = _frame_counter
                sid = _current_session
                lmu = _last_min_update

            if sid and fc % 10 == 0 and not _draining:
                try:
                    db.log_frame_emotions(sid, current_frame_emotions)
                    # Per-person logging (uses cached emotion results)
                    with _emotion_results_lock:
                        snapshot = dict(_emotion_results)
                    for tid, (ename, _) in snapshot.items():
                        if ename != 'neutral':
                            ekey = EMOTION_MAP.get(ename)
                            if ekey:
                                db.log_person_emotion(sid, tid, ekey)

                    current_minute = datetime.now().replace(second=0, microsecond=0)
                    if lmu is None or current_minute > lmu:
                        db.update_minute_summaries(sid)
                        with state_lock:
                            _last_min_update = current_minute
                except Exception as e:
                    print(f"[generate] DB error: {e}")

            # ── Blend & encode ─────────────────────────────────────────────────
            if detected_count > 0:
                frame = cv2.addWeighted(frame, 0.4, overlay, 0.6, 0)
            cv2.putText(frame, f"Persons detected: {detected_count}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

            _, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')

        except Exception as e:
            print(f"[generate] Error: {e}")
            import traceback
            traceback.print_exc()
            continue


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/video')
def video():
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/start')
def start():
    global _capturing, _current_session, _frame_counter, _last_min_update, _draining
    with state_lock:
        _capturing       = True
        _frame_counter   = 0
        _last_min_update = None
        _draining        = False
        _current_session = db.start_session()
        sid = _current_session
    with emotion_lock:
        for k in emotion_data:
            emotion_data[k] = 0
    # Clear stale emotion cache from previous session
    with _emotion_results_lock:
        _emotion_results.clear()
    return jsonify({"status": "started", "session_id": sid})


@app.route('/stop')
def stop():
    global _capturing, _current_session, _draining

    with state_lock:
        _capturing = False
        _draining  = True
        sid        = _current_session

    if not sid:
        return jsonify({"status": "stopped"})

    # Brief wait so the generator finishes any in-progress DB write
    time.sleep(0.3)

    try:
        db.end_session(sid)
        print(f"[stop] Session {sid} ended and stats calculated.")
    except Exception as e:
        print(f"[stop] Error ending session: {e}")
        with state_lock:
            _draining = False
        return jsonify({"error": str(e)}), 500

    with state_lock:
        _current_session = None
        _draining        = False

    return jsonify({"status": "stopped"})


@app.route('/status')
def status():
    with state_lock:
        return jsonify({"capturing": _capturing, "session_id": _current_session})


@app.route('/emotion_data')
def get_emotion_data():
    with emotion_lock:
        data = emotion_data.copy()
    return jsonify({"emotions": data})


# ── Camera settings ────────────────────────────────────────────────────────────
@app.route('/api/settings/camera', methods=['GET'])
def get_camera_settings():
    return jsonify({"rtsp_url": get_rtsp_url()})


@app.route('/api/settings/camera', methods=['POST'])
def update_camera_settings():
    try:
        payload = request.get_json()
        new_url = (payload or {}).get('rtsp_url', '').strip()
        if not new_url:
            return jsonify({"success": False, "error": "RTSP URL cannot be empty"}), 400
        if not new_url.startswith('rtsp://'):
            return jsonify({"success": False, "error": "Invalid RTSP URL format"}), 400
        if is_capturing():
            return jsonify({"success": False,
                            "error": "Stop analysis before changing camera settings"}), 400
        set_rtsp_url(new_url)
        return jsonify({"success": True, "rtsp_url": new_url})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── Session / report endpoints ─────────────────────────────────────────────────
@app.route('/api/sessions')
def api_sessions():
    sessions = db.get_all_sessions()
    return jsonify({"success": True, "sessions": sessions})


@app.route('/api/session/<int:session_id>')
def api_session_report(session_id):
    if session_id <= 0:
        return jsonify({"error": "Invalid session id"}), 400
    report = db.get_session_report(session_id)
    if not report:
        return jsonify({"error": "Session not found"}), 404
    report['top_emotions'] = db.get_top_emotions(session_id)
    return jsonify(report)


@app.route('/api/session/<int:session_id>/per-person')
def api_per_person_stats(session_id):
    if session_id <= 0:
        return jsonify({"success": False, "error": "Invalid session id"}), 400
    try:
        data = db.get_per_person_stats(session_id)
        return jsonify({"success": True, "per_person": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/session/<int:session_id>/delete', methods=['POST'])
def api_delete_session(session_id):
    if session_id <= 0:
        return jsonify({"success": False, "error": "Invalid session id"}), 400
    try:
        db.delete_session(session_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/session/<int:session_id>/export-pdf')
def api_export_pdf(session_id):
    if session_id <= 0:
        return jsonify({"error": "Invalid session id"}), 400
    report_data              = db.get_session_report(session_id)
    report_data['top_emotions'] = db.get_top_emotions(session_id)
    report_data['per_person']   = db.get_per_person_stats(session_id)
    generator = ReportGenerator()

    # Write to a temp file, stream it, then delete it automatically
    tmp = tempfile.NamedTemporaryFile(
        suffix='.pdf', prefix=f'sentivision_report_{session_id}_',
        delete=False
    )
    tmp_path = tmp.name
    tmp.close()

    generator.generate_report(report_data, tmp_path)
    return send_file(
        tmp_path,
        as_attachment=True,
        download_name=f'sentivision_report_session_{session_id}.pdf',
        max_age=0
    )


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    _validate_models()
    threading.Thread(target=capture_frames,  daemon=True, name="capture").start()
    threading.Thread(target=deepface_worker, daemon=True, name="deepface").start()
    app.run(host='0.0.0.0', port=5000, threaded=True)
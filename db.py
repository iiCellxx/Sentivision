import sqlite3
from datetime import datetime
from contextlib import contextmanager


class DatabaseHandler:
    def __init__(self, db_path='sentivision.db'):
        self.db_path = db_path
        self._init_lock = None   # not needed; init runs once at startup
        self.init_database()

    @contextmanager
    def get_connection(self):
        """Context manager for database connections with WAL mode."""
        conn = sqlite3.connect(self.db_path, timeout=10,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL mode allows concurrent readers while a writer is active
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_database(self):
        """Initialize database tables and indexes."""
        with self.get_connection() as conn:
            c = conn.cursor()

            c.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time   TIMESTAMP NOT NULL,
                    end_time     TIMESTAMP,
                    total_frames INTEGER DEFAULT 0,
                    status       TEXT DEFAULT 'active'
                )
            ''')

            c.execute('''
                CREATE TABLE IF NOT EXISTS frame_emotions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id   INTEGER NOT NULL,
                    timestamp    TIMESTAMP NOT NULL,
                    happy        INTEGER DEFAULT 0,
                    surprise     INTEGER DEFAULT 0,
                    sad          INTEGER DEFAULT 0,
                    fear         INTEGER DEFAULT 0,
                    angry        INTEGER DEFAULT 0,
                    disgust      INTEGER DEFAULT 0,
                    total_persons INTEGER DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            ''')

            c.execute('''
                CREATE TABLE IF NOT EXISTS minute_summaries (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id        INTEGER NOT NULL,
                    minute_mark       TIMESTAMP NOT NULL,
                    avg_happy         REAL DEFAULT 0,
                    avg_surprise      REAL DEFAULT 0,
                    avg_sad           REAL DEFAULT 0,
                    avg_fear          REAL DEFAULT 0,
                    avg_angry         REAL DEFAULT 0,
                    avg_disgust       REAL DEFAULT 0,
                    avg_total_persons REAL DEFAULT 0,
                    frame_count       INTEGER DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            ''')

            c.execute('''
                CREATE TABLE IF NOT EXISTS person_emotions (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id    INTEGER NOT NULL,
                    timestamp     TIMESTAMP NOT NULL,
                    person_number INTEGER NOT NULL,
                    emotion       TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            ''')

            c.execute('''
                CREATE TABLE IF NOT EXISTS overall_stats (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id         INTEGER NOT NULL UNIQUE,
                    total_happy        INTEGER DEFAULT 0,
                    total_surprise     INTEGER DEFAULT 0,
                    total_sad          INTEGER DEFAULT 0,
                    total_fear         INTEGER DEFAULT 0,
                    total_angry        INTEGER DEFAULT 0,
                    total_disgust      INTEGER DEFAULT 0,
                    total_detections   INTEGER DEFAULT 0,
                    dominant_emotion   TEXT,
                    positive_percentage REAL DEFAULT 0,
                    negative_percentage REAL DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            ''')

            # Indexes for fast per-session queries
            c.execute('''
                CREATE INDEX IF NOT EXISTS idx_frame_emotions_session
                ON frame_emotions(session_id)
            ''')
            c.execute('''
                CREATE INDEX IF NOT EXISTS idx_minute_summaries_session
                ON minute_summaries(session_id)
            ''')
            c.execute('''
                CREATE INDEX IF NOT EXISTS idx_person_emotions_session
                ON person_emotions(session_id)
            ''')
            c.execute('''
                CREATE INDEX IF NOT EXISTS idx_person_emotions_session_person
                ON person_emotions(session_id, person_number)
            ''')

    # ── Session lifecycle ──────────────────────────────────────────────────────

    def start_session(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO sessions (start_time, status) VALUES (?, 'active')",
                (datetime.now(),)
            )
            return c.lastrowid

    def end_session(self, session_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE sessions SET end_time=?, status='completed' WHERE id=?",
                (datetime.now(), session_id)
            )
            self._calculate_overall_stats(session_id, c)

    # ── Frame & per-person logging ─────────────────────────────────────────────

    def log_frame_emotions(self, session_id, emotions):
        """Insert one frame-emotion row and increment the session frame count."""
        total_persons = sum(emotions.values())
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute(
                '''INSERT INTO frame_emotions
                   (session_id, timestamp, happy, surprise, sad, fear, angry, disgust, total_persons)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    session_id, datetime.now(),
                    emotions.get('Happy',    0),
                    emotions.get('Surprise', 0),
                    emotions.get('Sad',      0),
                    emotions.get('Fear',     0),
                    emotions.get('Angry',    0),
                    emotions.get('Disgust',  0),
                    total_persons,
                )
            )
            c.execute(
                "UPDATE sessions SET total_frames = total_frames + 1 WHERE id = ?",
                (session_id,)
            )

    def log_person_emotion(self, session_id, person_number, emotion):
        """Log a single non-neutral per-person emotion detection."""
        if not emotion or emotion.lower() == 'neutral':
            return
        with self.get_connection() as conn:
            conn.execute(
                '''INSERT INTO person_emotions (session_id, timestamp, person_number, emotion)
                   VALUES (?, ?, ?, ?)''',
                (session_id, datetime.now(), person_number, emotion)
            )

    def update_minute_summaries(self, session_id):
        """Aggregate frame_emotions into minute_summaries for unseen minutes."""
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute(
                '''INSERT INTO minute_summaries
                   (session_id, minute_mark, avg_happy, avg_surprise, avg_sad,
                    avg_fear, avg_angry, avg_disgust, avg_total_persons, frame_count)
                   SELECT
                       ? AS session_id,
                       strftime('%Y-%m-%d %H:%M:00', timestamp) AS minute_mark,
                       AVG(happy), AVG(surprise), AVG(sad),
                       AVG(fear),  AVG(angry),   AVG(disgust),
                       AVG(total_persons), COUNT(*)
                   FROM frame_emotions
                   WHERE session_id = ?
                     AND strftime('%Y-%m-%d %H:%M:00', timestamp) NOT IN (
                         SELECT minute_mark FROM minute_summaries WHERE session_id = ?
                     )
                   GROUP BY strftime('%Y-%m-%d %H:%M:00', timestamp)''',
                (session_id, session_id, session_id)
            )

    # ── Statistics ─────────────────────────────────────────────────────────────

    def _calculate_overall_stats(self, session_id, cursor):
        cursor.execute(
            '''SELECT
                   SUM(happy)   AS total_happy,
                   SUM(surprise) AS total_surprise,
                   SUM(sad)     AS total_sad,
                   SUM(fear)    AS total_fear,
                   SUM(angry)   AS total_angry,
                   SUM(disgust) AS total_disgust,
                   SUM(total_persons) AS total_detections
               FROM frame_emotions WHERE session_id = ?''',
            (session_id,)
        )
        row = cursor.fetchone()
        if not row or not row['total_detections']:
            return

        total    = row['total_detections']
        positive = (row['total_happy'] or 0) + (row['total_surprise'] or 0)
        negative = (
            (row['total_sad']     or 0) + (row['total_fear']    or 0) +
            (row['total_angry']   or 0) + (row['total_disgust'] or 0)
        )

        emotions_dict = {
            'Happy':    row['total_happy']    or 0,
            'Surprise': row['total_surprise'] or 0,
            'Sad':      row['total_sad']      or 0,
            'Fear':     row['total_fear']     or 0,
            'Angry':    row['total_angry']    or 0,
            'Disgust':  row['total_disgust']  or 0,
        }
        # Guard: if all values are 0, skip
        if not any(emotions_dict.values()):
            return

        dominant_emotion = max(emotions_dict, key=emotions_dict.get)

        cursor.execute(
            '''INSERT OR REPLACE INTO overall_stats
               (session_id, total_happy, total_surprise, total_sad, total_fear,
                total_angry, total_disgust, total_detections,
                dominant_emotion, positive_percentage, negative_percentage)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                session_id,
                emotions_dict['Happy'],    emotions_dict['Surprise'],
                emotions_dict['Sad'],      emotions_dict['Fear'],
                emotions_dict['Angry'],    emotions_dict['Disgust'],
                total, dominant_emotion,
                round(positive / total * 100, 2),
                round(negative / total * 100, 2),
            )
        )

    # ── Queries ────────────────────────────────────────────────────────────────

    def get_session_report(self, session_id):
        with self.get_connection() as conn:
            c = conn.cursor()

            c.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = c.fetchone()
            if not row:
                return None
            session = dict(row)

            c.execute("SELECT * FROM overall_stats WHERE session_id = ?", (session_id,))
            overall = c.fetchone()

            c.execute(
                "SELECT * FROM minute_summaries WHERE session_id = ? ORDER BY minute_mark",
                (session_id,)
            )
            minute_summaries = [dict(r) for r in c.fetchall()]

            # Last 60 seconds of frame-level data (labelled correctly)
            c.execute(
                '''SELECT * FROM frame_emotions
                   WHERE session_id = ?
                     AND timestamp >= datetime('now', '-60 seconds')
                   ORDER BY timestamp DESC''',
                (session_id,)
            )
            realtime_data = [dict(r) for r in c.fetchall()]

            return {
                'session':          session,
                'overall_stats':    dict(overall) if overall else None,
                'minute_summaries': minute_summaries,
                'realtime_data':    realtime_data,
            }

    def get_all_sessions(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute(
                '''SELECT s.*, os.dominant_emotion,
                          os.positive_percentage, os.negative_percentage
                   FROM sessions s
                   LEFT JOIN overall_stats os ON s.id = os.session_id
                   ORDER BY s.start_time DESC'''
            )
            return [dict(r) for r in c.fetchall()]

    def get_top_emotions(self, session_id, limit=6):
        """Return emotions ranked by total detection count (single efficient query)."""
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute(
                '''SELECT emotion, SUM(cnt) AS count
                   FROM (
                       SELECT 'Happy'    AS emotion, SUM(happy)    AS cnt FROM frame_emotions WHERE session_id = ?
                       UNION ALL
                       SELECT 'Surprise',             SUM(surprise)       FROM frame_emotions WHERE session_id = ?
                       UNION ALL
                       SELECT 'Sad',                  SUM(sad)            FROM frame_emotions WHERE session_id = ?
                       UNION ALL
                       SELECT 'Fear',                 SUM(fear)           FROM frame_emotions WHERE session_id = ?
                       UNION ALL
                       SELECT 'Angry',                SUM(angry)          FROM frame_emotions WHERE session_id = ?
                       UNION ALL
                       SELECT 'Disgust',              SUM(disgust)        FROM frame_emotions WHERE session_id = ?
                   )
                   GROUP BY emotion
                   ORDER BY count DESC
                   LIMIT ?''',
                (session_id,) * 6 + (limit,)
            )
            return [dict(r) for r in c.fetchall()]

    def get_per_person_stats(self, session_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute(
                '''SELECT person_number,
                          MIN(timestamp) AS first_seen,
                          MAX(timestamp) AS last_seen,
                          COUNT(*) AS total,
                          GROUP_CONCAT(emotion, '|') AS emotions_concat
                   FROM person_emotions
                   WHERE session_id = ?
                   GROUP BY person_number
                   ORDER BY person_number''',
                (session_id,)
            )
            persons = c.fetchall()

        result = []
        for p in persons:
            pnum = p['person_number']
            total = p['total']
            if total == 0:
                continue

            emotions_concat = p['emotions_concat'] or ''
            emotion_counts = {'Happy': 0, 'Surprise': 0, 'Sad': 0, 'Fear': 0, 'Angry': 0, 'Disgust': 0}
            for em in emotions_concat.split('|'):
                if em in emotion_counts:
                    emotion_counts[em] += 1

            duration_display = "0s"
            try:
                t1 = _parse_ts(p['first_seen'])
                t2 = _parse_ts(p['last_seen'])
                secs = int((t2 - t1).total_seconds())
                mm, ss = divmod(secs, 60)
                duration_display = f"{mm}m {ss}s" if mm > 0 else f"{ss}s"
            except Exception:
                pass

            positive = sum(emotion_counts.get(e, 0) for e in ('Happy', 'Surprise'))
            negative = sum(emotion_counts.get(e, 0) for e in ('Sad', 'Fear', 'Angry', 'Disgust'))
            dominant = max(emotion_counts, key=emotion_counts.get) if emotion_counts else 'N/A'

            result.append({
                'person_number': pnum,
                'first_seen': p['first_seen'],
                'last_seen': p['last_seen'],
                'duration': duration_display,
                'emotion_counts': emotion_counts,
                'total_detections': total,
                'dominant_emotion': dominant,
                'positive_percentage': round(positive / total * 100, 1),
                'negative_percentage': round(negative / total * 100, 1),
            })

        return result

    ALLOWED_DELETE_TABLES = {
        'frame_emotions': 'session_id',
        'minute_summaries': 'session_id',
        'overall_stats': 'session_id',
        'person_emotions': 'session_id',
        'sessions': 'id',
    }

    def delete_session(self, session_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            for table, col in self.ALLOWED_DELETE_TABLES.items():
                c.execute(f'DELETE FROM {table} WHERE {col} = ?', (session_id,))


# ── Helper ─────────────────────────────────────────────────────────────────────
def _parse_ts(ts: str) -> datetime:
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(ts)
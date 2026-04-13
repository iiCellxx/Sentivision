"""
seed_db.py — Inserts 4 demo sessions (51–54) into sentivision.db.
Total: 32 customers across all sessions.
Usage:  python seed_db.py
"""

import sqlite3
import random
from datetime import datetime, timedelta
import os

DB_PATH = 'sentivision.db'

_ID_LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ'   # 23 chars, no I/O/W

def track_id_to_alias(tid: int) -> str:
    base = len(_ID_LETTERS)
    i    = max(0, tid - 1)
    return (
        _ID_LETTERS[(i // (base * base)) % base] +
        _ID_LETTERS[(i // base) % base] +
        _ID_LETTERS[i % base]
    )

# ── Emotion count templates by dominant emotion ────────────────────────────────
# counts: [Happy, Surprise, Sad, Fear, Angry, Disgust]
def make_counts(dominant: str) -> list:
    templates = {
        'Happy':    [80, 20,  5,  2,  3,  1],
        'Surprise': [15, 90, 10,  8,  5,  3],
        'Sad':      [10,  3, 95, 15,  8,  5],
        'Fear':     [ 5,  8, 20, 85,  6,  4],
        'Angry':    [ 5,  3, 15,  8, 90, 25],
        'Disgust':  [ 4,  2, 12,  6, 20, 80],
    }
    base = templates[dominant][:]
    # Add small random noise so each person feels unique
    return [max(0, v + random.randint(-3, 3)) for v in base]


# ── Session definitions ────────────────────────────────────────────────────────
SESSIONS = [
    {
        'id':         51,
        'start':      datetime(2026, 4, 13, 18, 30, 0),   # April 13, 6:30 PM
        'duration':   36,                                   # minutes → ends 7:06 PM
        'customers': [
            (1,  'Sad'),
            (2,  'Sad'),
            (3,  'Sad'),
            (4,  'Sad'),
            (5,  'Sad'),
            (6,  'Angry'),
            (7,  'Angry'),
            (8,  'Happy'),
            (9,  'Happy'),
            (10, 'Happy'),
        ],
    },
    {
        'id':         52,
        'start':      datetime(2026, 4, 15, 18, 30, 0),   # April 15, 6:30 PM
        'duration':   43,                                   # ends 7:13 PM
        'customers': [
            (1, 'Sad'),
            (2, 'Happy'),
            (3, 'Happy'),
            (4, 'Sad'),
            (5, 'Angry'),
            (6, 'Sad'),
        ],
    },
    {
        'id':         53,
        'start':      datetime(2026, 4, 16, 19, 0, 0),    # April 16, 7:00 PM
        'duration':   42,                                   # ends 7:42 PM
        'customers': [
            (1, 'Angry'),
            (2, 'Surprise'),
            (3, 'Sad'),
            (4, 'Happy'),
        ],
    },
    {
        'id':         54,
        'start':      datetime(2026, 4, 17, 19, 20, 0),   # April 17, 7:20 PM
        'duration':   80,                                   # ends 8:40 PM
        'customers': [
            (1,  'Sad'),
            (2,  'Sad'),
            (3,  'Angry'),
            (4,  'Happy'),
            (5,  'Surprise'),
            (6,  'Sad'),
            (7,  'Sad'),
            (8,  'Happy'),
            (9,  'Angry'),
            (10, 'Angry'),
            (11, 'Angry'),
            (12, 'Angry'),
        ],
    },
]


def seed_session(cursor, session_def: dict):
    sid           = session_def['id']
    start_time    = session_def['start']
    duration_mins = session_def['duration']
    end_time      = start_time + timedelta(minutes=duration_mins)
    total_frames  = duration_mins * 60
    customers     = session_def['customers']
    emotions_list = ['Happy', 'Surprise', 'Sad', 'Fear', 'Angry', 'Disgust']

    # ── Guard: skip if already exists ─────────────────────────────────────────
    cursor.execute("SELECT id FROM sessions WHERE id = ?", (sid,))
    if cursor.fetchone():
        print(f"[seed] Session #{sid} already exists — skipping.")
        return

    print(f"[seed] Creating Session #{sid}  |  {start_time}  →  {end_time}")

    cursor.execute(
        "INSERT INTO sessions (id, start_time, end_time, total_frames, status) "
        "VALUES (?, ?, ?, ?, 'completed')",
        (sid, start_time, end_time, total_frames)
    )

    all_totals = [0] * 6

    for person_num, dominant in customers:
        counts = make_counts(dominant)

        offset_start = random.randint(0, max(0, duration_mins - 10))
        offset_end   = offset_start + random.randint(5, min(30, duration_mins - offset_start))
        win_start    = start_time + timedelta(minutes=offset_start)
        win_end      = start_time + timedelta(minutes=offset_end)
        window_secs  = max(int((win_end - win_start).total_seconds()), 1)

        rows = []
        for idx, cnt in enumerate(counts):
            ename = emotions_list[idx]
            for _ in range(cnt):
                ts = win_start + timedelta(seconds=random.randint(0, window_secs - 1))
                rows.append((sid, ts, person_num, ename))
        random.shuffle(rows)

        cursor.executemany(
            "INSERT INTO person_emotions (session_id, timestamp, person_number, emotion) "
            "VALUES (?, ?, ?, ?)",
            rows
        )
        for i in range(6):
            all_totals[i] += counts[i]

    # ── Frame emotions ─────────────────────────────────────────────────────────
    frame_interval = max(1, int((end_time - start_time).total_seconds() / 100))
    dominant_idx   = all_totals.index(max(all_totals))
    for i in range(100):
        ft   = start_time + timedelta(seconds=i * frame_interval)
        vals = [random.randint(0, 1)] * 6
        vals[dominant_idx] = random.randint(3, 8)
        cursor.execute(
            "INSERT INTO frame_emotions "
            "(session_id, timestamp, happy, surprise, sad, fear, angry, disgust, total_persons) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, ft, vals[0], vals[1], vals[2], vals[3], vals[4], vals[5],
             random.randint(1, len(customers)))
        )

    # ── Overall stats ──────────────────────────────────────────────────────────
    grand_total = sum(all_totals)
    grand_pos   = all_totals[0] + all_totals[1]
    grand_neg   = sum(all_totals[2:])
    grand_dom   = emotions_list[all_totals.index(max(all_totals))]

    cursor.execute(
        "INSERT OR REPLACE INTO overall_stats "
        "(session_id, total_happy, total_surprise, total_sad, total_fear, "
        " total_angry, total_disgust, total_detections, dominant_emotion, "
        " positive_percentage, negative_percentage) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sid,
         all_totals[0], all_totals[1], all_totals[2],
         all_totals[3], all_totals[4], all_totals[5],
         grand_total, grand_dom,
         round(grand_pos / grand_total * 100, 2),
         round(grand_neg / grand_total * 100, 2))
    )

    # ── Minute summaries ───────────────────────────────────────────────────────
    for i in range(0, duration_mins, 5):
        mt = start_time + timedelta(minutes=i)
        cursor.execute(
            "INSERT INTO minute_summaries "
            "(session_id, minute_mark, avg_happy, avg_surprise, avg_sad, "
            " avg_fear, avg_angry, avg_disgust, avg_total_persons, frame_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, mt,
             random.uniform(0, 5),  random.uniform(0, 2),
             random.uniform(0, 10), random.uniform(0, 3),
             random.uniform(0, 4),  random.uniform(0, 2),
             random.uniform(1, len(customers)), 10)
        )

    print(f"       Customers : {len(customers)}")
    print(f"       Aliases   : {', '.join(track_id_to_alias(p) for p, _ in customers)}")
    print(f"       Totals    → Happy:{all_totals[0]}  Surprise:{all_totals[1]}  "
          f"Sad:{all_totals[2]}  Fear:{all_totals[3]}  "
          f"Angry:{all_totals[4]}  Disgust:{all_totals[5]}")
    print(f"       Dominant  : {grand_dom}  |  "
          f"Positive: {round(grand_pos/grand_total*100,1)}%  "
          f"Negative: {round(grand_neg/grand_total*100,1)}%")


def seed_data():
    if not os.path.exists(DB_PATH):
        print(f"[seed] Database '{DB_PATH}' not found. Run the app once to initialise it.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    total_customers = sum(len(s['customers']) for s in SESSIONS)
    print(f"[seed] Seeding {len(SESSIONS)} sessions — {total_customers} customers total\n")

    for session_def in SESSIONS:
        seed_session(cursor, session_def)
        print()

    conn.commit()
    conn.close()
    print("[seed] Done! ✓")


if __name__ == '__main__':
    seed_data()
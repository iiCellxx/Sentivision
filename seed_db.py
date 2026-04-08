"""
seed_db.py — Inserts a realistic demo session into sentivision.db.
Assigns the next available session ID (MAX + 1) so real sessions are never overwritten.
Usage:  python seed_db.py
"""

import sqlite3
import random
from datetime import datetime, timedelta
import os

DB_PATH = 'sentivision.db'

# ── Track-ID → 3-letter alias (mirrors app.py / script.js logic) ──────────────
_ID_LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ'   # 23 chars, no I/O/W

def track_id_to_alias(tid: int) -> str:
    base = len(_ID_LETTERS)
    i    = max(0, tid - 1)
    return (
        _ID_LETTERS[(i // (base * base)) % base] +
        _ID_LETTERS[(i // base) % base] +
        _ID_LETTERS[i % base]
    )


def seed_data():
    if not os.path.exists(DB_PATH):
        print(f"[seed] Database '{DB_PATH}' not found. Run the app once to initialise it.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    # ── Pick a safe session ID ─────────────────────────────────────────────────
    cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM sessions")
    session_id = cursor.fetchone()['next_id']
    print(f"[seed] Creating demo session #{session_id}")

    # ── Session timing ─────────────────────────────────────────────────────────
    end_time      = datetime.now() - timedelta(hours=random.randint(1, 48),
                                               minutes=random.randint(0, 59))
    duration_mins = random.randint(45, 75)
    start_time    = end_time - timedelta(minutes=duration_mins)
    total_frames  = duration_mins * 60

    cursor.execute(
        "INSERT INTO sessions (id, start_time, end_time, total_frames, status) "
        "VALUES (?, ?, ?, ?, 'completed')",
        (session_id, start_time, end_time, total_frames)
    )

    # ── Customer profiles ──────────────────────────────────────────────────────
    # counts: [Happy, Surprise, Sad, Fear, Angry, Disgust]
    emotions_list = ['Happy', 'Surprise', 'Sad', 'Fear', 'Angry', 'Disgust']

    profiles = [
        {'person_number': 1,  'counts': [30,  2,  120, 10,  5,  2]},   # Sad-dominant
        {'person_number': 2,  'counts': [2,  1,  120,  5, 12,  8]},
        {'person_number': 3,  'counts': [10, 5,  200, 20, 15,  5]},
        {'person_number': 4,  'counts': [0,  0,  110, 10,  2,  1]},
        {'person_number': 5,  'counts': [8,  4,  180, 15, 10, 10]},
        {'person_number': 6,  'counts': [2,  1,   20, 10, 90, 30]},   # Angry-dominant
        {'person_number': 7,  'counts': [5,  5,   15,  5, 75, 40]},
        {'person_number': 8,  'counts': [60, 20,   5,  2,  1,  0]},   # Happy-dominant
        {'person_number': 9,  'counts': [50, 15,  10,  5,  5,  2]},
        {'person_number': 10, 'counts': [70, 25,   2,  1,  0,  0]},
    ]

    all_totals = [0] * 6

    for p in profiles:
        counts     = p['counts']
        person_num = p['person_number']

        offset_start = random.randint(0, max(0, duration_mins - 10))
        offset_end   = offset_start + random.randint(5, min(45, duration_mins - offset_start))
        win_start    = start_time + timedelta(minutes=offset_start)
        win_end      = start_time + timedelta(minutes=offset_end)
        window_secs  = max(int((win_end - win_start).total_seconds()), 1)

        rows = []
        for idx, cnt in enumerate(counts):
            ename = emotions_list[idx]
            for _ in range(cnt):
                ts = win_start + timedelta(seconds=random.randint(0, window_secs - 1))
                rows.append((session_id, ts, person_num, ename))
        random.shuffle(rows)

        cursor.executemany(
            "INSERT INTO person_emotions (session_id, timestamp, person_number, emotion) "
            "VALUES (?, ?, ?, ?)",
            rows
        )
        for i in range(6):
            all_totals[i] += counts[i]

    # ── Frame emotions (drives Top Emotions & trend charts) ────────────────────
    frame_interval = int((end_time - start_time).total_seconds() / 100)
    for i in range(100):
        ft = start_time + timedelta(seconds=i * frame_interval)
        cursor.execute(
            "INSERT INTO frame_emotions "
            "(session_id, timestamp, happy, surprise, sad, fear, angry, disgust, total_persons) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, ft,
             random.randint(0, 2), random.randint(0, 1),
             random.randint(5, 12), random.randint(0, 2),
             random.randint(0, 3), random.randint(0, 1),
             random.randint(1, 5))
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
        (session_id,
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
            (session_id, mt,
             random.uniform(0, 5),  random.uniform(0, 2),
             random.uniform(0, 10), random.uniform(0, 3),
             random.uniform(0, 4),  random.uniform(0, 2),
             random.uniform(1, 5),  10)
        )

    conn.commit()
    conn.close()

    print(f"[seed] Session #{session_id} seeded with {len(profiles)} customers.")
    print(f"       Totals  → Happy:{all_totals[0]}  Surprise:{all_totals[1]}  "
          f"Sad:{all_totals[2]}  Fear:{all_totals[3]}  "
          f"Angry:{all_totals[4]}  Disgust:{all_totals[5]}")
    print(f"       Dominant: {grand_dom}  |  "
          f"Positive: {round(grand_pos/grand_total*100,1)}%  "
          f"Negative: {round(grand_neg/grand_total*100,1)}%")
    print(f"       Customer aliases:")
    for p in profiles:
        print(f"         Person #{p['person_number']:>2} → {track_id_to_alias(p['person_number'])}")


if __name__ == '__main__':
    seed_data()
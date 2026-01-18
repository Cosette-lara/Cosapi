import os
import uuid
import sqlite3
import hashlib
from datetime import datetime, timezone, date
from typing import Optional, Dict, Any, List

import requests

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "cosapi_voice_poc.db")

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
AUDIO_DIR = os.path.join(UPLOAD_DIR, "audio")
PHOTO_DIR = os.path.join(UPLOAD_DIR, "photos")

os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(PHOTO_DIR, exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso_date_utc() -> str:
    # yyyy-mm-dd (UTC)
    return datetime.now(timezone.utc).date().isoformat()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def init_db():
    """
    Crea tabla y MIGRA columnas faltantes para evitar errores por BD antigua.
    """
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS reports (
        report_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        created_date TEXT,            -- yyyy-mm-dd (UTC)
        user_email TEXT,
        project_id TEXT,
        audio_path TEXT,
        audio_sha256 TEXT,
        transcript_text TEXT,
        transcript_sha256 TEXT,
        photo_url TEXT,               -- URL para front: /uploads/photos/xxx.jpg
        photo_sha256 TEXT
    )
    """)

    cur.execute("PRAGMA table_info(reports)")
    existing = {row[1] for row in cur.fetchall()}

    def ensure_col(name: str, coltype: str):
        if name not in existing:
            cur.execute(f"ALTER TABLE reports ADD COLUMN {name} {coltype}")

    ensure_col("created_date", "TEXT")
    ensure_col("user_email", "TEXT")
    ensure_col("project_id", "TEXT")
    ensure_col("audio_path", "TEXT")
    ensure_col("audio_sha256", "TEXT")
    ensure_col("transcript_text", "TEXT")
    ensure_col("transcript_sha256", "TEXT")
    ensure_col("photo_url", "TEXT")
    ensure_col("photo_sha256", "TEXT")

    con.commit()
    con.close()


def deepgram_transcribe(audio_path: str, mime_type: str) -> str:
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise RuntimeError("Falta DEEPGRAM_API_KEY en variables de entorno.")

    url = "https://api.deepgram.com/v1/listen"
    params = {"language": "es", "punctuate": "true", "smart_format": "true"}
    headers = {"Authorization": f"Token {api_key}", "Content-Type": mime_type}

    with open(audio_path, "rb") as f:
        resp = requests.post(url, params=params, headers=headers, data=f, timeout=120)

    if resp.status_code != 200:
        raise RuntimeError(f"Deepgram error {resp.status_code}: {resp.text}")

    data = resp.json()
    transcript = (
        data.get("results", {})
            .get("channels", [{}])[0]
            .get("alternatives", [{}])[0]
            .get("transcript", "")
    )
    return (transcript or "").strip()


def create_report(
    audio_path: str,
    audio_mime: str,
    photo_url: Optional[str],
    photo_disk_path: Optional[str],
    user_email: Optional[str],
    project_id: Optional[str]
) -> Dict[str, Any]:
    init_db()

    report_id = str(uuid.uuid4())
    created_at = now_iso()
    created_date = iso_date_utc()

    audio_hash = sha256_file(audio_path)

    transcript = deepgram_transcribe(audio_path, mime_type=audio_mime)
    transcript_hash = sha256_text(transcript)

    photo_hash = sha256_file(photo_disk_path) if photo_disk_path else None

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO reports (
            report_id, created_at, created_date,
            user_email, project_id,
            audio_path, audio_sha256,
            transcript_text, transcript_sha256,
            photo_url, photo_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        report_id, created_at, created_date,
        user_email, project_id,
        audio_path, audio_hash,
        transcript, transcript_hash,
        photo_url, photo_hash
    ))
    con.commit()
    con.close()

    return {
        "report_id": report_id,
        "created_at": created_at,
        "created_date": created_date,
        "project_id": project_id,
        "user_email": user_email,
        "transcript_text": transcript,
        "photo_url": photo_url
    }


def update_transcript(report_id: str, new_text: str) -> Dict[str, Any]:
    init_db()
    updated_hash = sha256_text(new_text)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        UPDATE reports
        SET transcript_text=?, transcript_sha256=?
        WHERE report_id=?
    """, (new_text, updated_hash, report_id))
    con.commit()
    con.close()

    return {"report_id": report_id, "saved": True}


def list_reports(limit: int = 50) -> List[Dict[str, Any]]:
    init_db()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("""
        SELECT report_id, created_at, created_date, user_email, project_id, transcript_text, photo_url
        FROM reports
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()

    # snippet para lista
    for r in rows:
        t = (r.get("transcript_text") or "").strip()
        r["transcript_snippet"] = (t[:140] + "…") if len(t) > 140 else t
    return rows


def get_report(report_id: str) -> Optional[Dict[str, Any]]:
    init_db()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM reports WHERE report_id=?", (report_id,))
    row = cur.fetchone()
    con.close()
    return dict(row) if row else None


def list_reports_by_date(created_date: str) -> List[Dict[str, Any]]:
    init_db()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("""
        SELECT report_id, created_at, user_email, project_id, transcript_text, photo_url
        FROM reports
        WHERE created_date = ?
        ORDER BY created_at ASC
    """, (created_date,))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows

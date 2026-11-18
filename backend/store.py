import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA


class Store:
    def __init__(self, root: Path = DATA):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        # Keep the existing database filename so a branding change preserves history.
        self.path = root / "soundscribe.db"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
                    created_at TEXT NOT NULL, status TEXT NOT NULL,
                    language TEXT NOT NULL, model TEXT NOT NULL, engine TEXT NOT NULL,
                    vocabulary TEXT NOT NULL DEFAULT '', duration REAL NOT NULL DEFAULT 0,
                    processed REAL NOT NULL DEFAULT 0, error TEXT
                );
                CREATE TABLE IF NOT EXISTS segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    start REAL NOT NULL, end REAL NOT NULL,
                    text TEXT NOT NULL, original_text TEXT NOT NULL,
                    review INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS segments_session ON segments(session_id, start);
            """)
            db.execute(
                "UPDATE sessions SET status='interrupted', error=? "
                "WHERE status IN ('preparing','recording','transcribing','stopping')",
                ("服务已重启。已完成的文字和录音仍保留，可导出或重新导入录音。",),
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, settings, source: str):
        sid = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO sessions(id,title,source,created_at,status,language,model,engine,vocabulary) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    sid,
                    settings.title,
                    source,
                    datetime.now(timezone.utc).isoformat(),
                    "preparing",
                    settings.language,
                    settings.model,
                    settings.engine,
                    settings.vocabulary,
                ),
            )
        (self.root / sid).mkdir()
        return sid

    def update(self, sid, **fields):
        allowed = {"title", "status", "duration", "processed", "error", "engine"}
        if not fields.keys() <= allowed:
            raise ValueError("Invalid session fields")
        with self.connect() as db:
            db.execute(
                f"UPDATE sessions SET {','.join(key + '=?' for key in fields)} WHERE id=?",
                (*fields.values(), sid),
            )

    def get(self, sid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["segments"] = [
                dict(s)
                for s in db.execute("SELECT * FROM segments WHERE session_id=? ORDER BY start,id", (sid,))
            ]
            return result

    def list(self):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT s.*, (SELECT COUNT(*) FROM segments WHERE session_id=s.id) AS segment_count "
                    "FROM sessions s ORDER BY created_at DESC"
                )
            ]

    def add_segments(self, sid, segments):
        with self.connect() as db:
            db.executemany(
                "INSERT INTO segments(session_id,start,end,text,original_text,review) VALUES(?,?,?,?,?,?)",
                [(sid, s["start"], s["end"], s["text"], s["text"], int(s["review"])) for s in segments],
            )

    def edit(self, sid, segment_id, text):
        with self.connect() as db:
            return db.execute(
                "UPDATE segments SET text=?,review=0 WHERE id=? AND session_id=?",
                (text, segment_id, sid),
            ).rowcount

    def audio_path(self, sid):
        # Only use database-backed IDs; never resolve a user-controlled filesystem path.
        if len(sid) != 32 or any(c not in "0123456789abcdef" for c in sid):
            raise ValueError("Invalid session ID")
        return self.root / sid / "audio.wav"


def timestamp(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def markdown(session):
    title = session["title"].replace("\n", " ").replace("\r", " ")
    lines = [
        f"# {title}",
        "",
        f"- 创建时间：{session['created_at']}",
        f"- 识别模型：Whisper {session['model']}",
        f"- 音频时长：{timestamp(session['duration'])}",
        "",
        "## 转写原文",
        "",
    ]
    for segment in session["segments"]:
        review = "（待校对）" if segment["review"] else ""
        lines += [f"### {timestamp(segment['start'])}{review}", "", segment["text"], ""]
    return "\n".join(lines)

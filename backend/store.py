import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA
from .timeformat import format_created_at, normalize_creation_header


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
                CREATE TABLE IF NOT EXISTS markdown_polishes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    original_content TEXT NOT NULL, result_content TEXT NOT NULL,
                    created_at TEXT NOT NULL, reverted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS polishes_session ON markdown_polishes(session_id, id);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
            if "audio_source" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN audio_source TEXT NOT NULL DEFAULT 'system'")
            if "markdown_content" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN markdown_content TEXT")
            segment_columns = {row["name"] for row in db.execute("PRAGMA table_info(segments)")}
            if "kind" not in segment_columns:
                db.execute("ALTER TABLE segments ADD COLUMN kind TEXT NOT NULL DEFAULT 'text'")
            if "photo_filename" not in segment_columns:
                db.execute("ALTER TABLE segments ADD COLUMN photo_filename TEXT")
            self._normalize_creation_headers(db)
            db.execute(
                "UPDATE sessions SET status='interrupted', error=? "
                "WHERE status IN ('preparing','recording','pausing','paused','resuming','transcribing','stopping')",
                ("服务已重启。已完成的文字仍保留，可校对并导出。",),
            )

    @staticmethod
    def _normalize_creation_headers(db):
        # Normalize both current notes and undo snapshots together so revisions remain usable.
        for row in db.execute(
            "SELECT id,created_at,markdown_content FROM sessions "
            "WHERE instr(markdown_content, '- 创建时间：' || created_at) > 0"
        ).fetchall():
            content = normalize_creation_header(row["markdown_content"], row["created_at"])
            if content != row["markdown_content"]:
                db.execute("UPDATE sessions SET markdown_content=? WHERE id=?", (content, row["id"]))
        for row in db.execute(
            "SELECT p.id,s.created_at,p.original_content,p.result_content "
            "FROM markdown_polishes p JOIN sessions s ON s.id=p.session_id "
            "WHERE instr(p.original_content, '- 创建时间：' || s.created_at) > 0 "
            "OR instr(p.result_content, '- 创建时间：' || s.created_at) > 0"
        ).fetchall():
            original = normalize_creation_header(row["original_content"], row["created_at"])
            result = normalize_creation_header(row["result_content"], row["created_at"])
            if original != row["original_content"] or result != row["result_content"]:
                db.execute("UPDATE markdown_polishes SET original_content=?,result_content=? WHERE id=?",
                           (original, result, row["id"]))

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

    def create(self, settings):
        sid = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO sessions(id,title,source,created_at,status,language,model,engine,audio_source) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    sid,
                    settings.title,
                    settings.audio_source,
                    datetime.now(timezone.utc).isoformat(),
                    "preparing",
                    settings.language,
                    settings.model,
                    settings.engine,
                    settings.audio_source,
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
            result.pop("vocabulary", None)  # Retain the legacy database column for existing installations.
            result["title"] = markdown_title(result.get("markdown_content")) or result["title"]
            result["segments"] = [
                dict(s)
                for s in db.execute("SELECT * FROM segments WHERE session_id=? ORDER BY start,id", (sid,))
            ]
            revision = db.execute(
                "SELECT id,result_content FROM markdown_polishes WHERE session_id=? AND reverted_at IS NULL "
                "ORDER BY id DESC LIMIT 1", (sid,),
            ).fetchone()
            result["polish_revision"] = (
                revision["id"] if revision and revision["result_content"] == markdown(result) else None
            )
            return result

    def get_status(self, sid):
        with self.connect() as db:
            row = db.execute("SELECT status FROM sessions WHERE id=?", (sid,)).fetchone()
            return row["status"] if row else None

    def list(self):
        with self.connect() as db:
            records = [
                dict(row)
                for row in db.execute(
                    "SELECT s.*, (SELECT COUNT(*) FROM segments WHERE session_id=s.id) AS segment_count "
                    "FROM sessions s ORDER BY created_at DESC"
                )
            ]
            for record in records:
                record.pop("vocabulary", None)
                record["title"] = markdown_title(record.get("markdown_content")) or record["title"]
            return records

    def add_segments(self, sid, segments):
        segments = list(segments)
        with self.connect() as db:
            db.executemany(
                "INSERT INTO segments(session_id,start,end,text,original_text,review) VALUES(?,?,?,?,?,?)",
                [(sid, s["start"], s["end"], s["text"], s["text"], int(s["review"])) for s in segments],
            )
            added = "\n".join(filter(None, (segment_markdown(segment) for segment in segments)))
            if added:
                row = db.execute("SELECT markdown_content FROM sessions WHERE id=?", (sid,)).fetchone()
                if row is not None and row["markdown_content"] is not None:
                    db.execute("UPDATE sessions SET markdown_content=? WHERE id=?",
                               (append_markdown(row["markdown_content"], added), sid))

    def edit(self, sid, segment_id, text):
        with self.connect() as db:
            return db.execute(
                "UPDATE segments SET text=?,review=0 WHERE id=? AND session_id=? AND kind='text'",
                (text, segment_id, sid),
            ).rowcount

    def delete_segment(self, sid, segment_id):
        photo = None
        with self.connect() as db:
            row = db.execute(
                "SELECT kind, photo_filename, text FROM segments WHERE id=? AND session_id=?",
                (segment_id, sid),
            ).fetchone()
            if row is None:
                return False
            db.execute("DELETE FROM segments WHERE id=? AND session_id=?", (segment_id, sid))
            if row["kind"] == "image" and row["photo_filename"]:
                photo = row["photo_filename"]
                markdown_row = db.execute(
                    "SELECT markdown_content FROM sessions WHERE id=?", (sid,)
                ).fetchone()
                if markdown_row and markdown_row["markdown_content"] is not None:
                    db.execute(
                        "UPDATE sessions SET markdown_content=? WHERE id=?",
                        (remove_photo_markdown(markdown_row["markdown_content"], photo), sid),
                    )
        if photo:
            (self.root / sid / "photo" / photo).unlink(missing_ok=True)
        return True

    def replace_screenshot(self, sid, segment_id, data):
        path = None
        old_path = None
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT start, photo_filename FROM segments "
                    "WHERE id=? AND session_id=? AND kind='image'",
                    (segment_id, sid),
                ).fetchone()
                if row is None:
                    return False
                filename = uuid.uuid4().hex + ".png"
                directory = self.root / sid / "photo"
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / filename
                path.write_bytes(data)
                text = f"![截图 {timestamp(row['start'])}](photo/{filename})"
                db.execute(
                    "UPDATE segments SET text=?, original_text=?, photo_filename=? "
                    "WHERE id=? AND session_id=? AND kind='image'",
                    (text, text, filename, segment_id, sid),
                )
                markdown_row = db.execute(
                    "SELECT markdown_content FROM sessions WHERE id=?", (sid,)
                ).fetchone()
                if markdown_row and markdown_row["markdown_content"] is not None and row["photo_filename"]:
                    db.execute(
                        "UPDATE sessions SET markdown_content=? WHERE id=?",
                        (
                            replace_photo_markdown(
                                markdown_row["markdown_content"],
                                row["photo_filename"],
                                filename,
                                text,
                            ),
                            sid,
                        ),
                    )
                if row["photo_filename"] and row["photo_filename"] != filename:
                    old_path = directory / row["photo_filename"]
            if old_path:
                old_path.unlink(missing_ok=True)
            return True
        except Exception:
            if path:
                path.unlink(missing_ok=True)
            raise

    def add_screenshot(self, sid, data, at=None):
        path = None
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                session = db.execute("SELECT duration,markdown_content FROM sessions WHERE id=?", (sid,)).fetchone()
                if session is None:
                    return False
                moment = min(max(0, at if at is not None else session["duration"]), session["duration"])
                filename = uuid.uuid4().hex + ".png"
                directory = self.root / sid / "photo"
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / filename
                path.write_bytes(data)
                text = f"![截图 {timestamp(moment)}](photo/{filename})"
                db.execute(
                    "INSERT INTO segments(session_id,start,end,text,original_text,review,kind,photo_filename) "
                    "VALUES(?,?,?,?,?,0,'image',?)", (sid, moment, moment, text, text, filename),
                )
                if session["markdown_content"] is not None:
                    db.execute("UPDATE sessions SET markdown_content=? WHERE id=?",
                               (append_markdown(session["markdown_content"], "\n\n" + text + "\n\n"), sid))
            return True
        except Exception:
            if path:
                path.unlink(missing_ok=True)
            raise

    def photo_path(self, sid, filename):
        if not re.fullmatch(r"[a-f0-9]{32}\.png", filename):
            return None
        with self.connect() as db:
            found = db.execute("SELECT 1 FROM segments WHERE session_id=? AND photo_filename=? AND kind='image'",
                               (sid, filename)).fetchone()
        path = self.root / sid / "photo" / filename
        return path if found and path.is_file() else None

    def save_markdown(self, sid, content, base_content=None):
        with self.connect() as db:
            row = db.execute("SELECT markdown_content FROM sessions WHERE id=?", (sid,)).fetchone()
            if row is None:
                return 0
            if base_content is not None:
                current = row["markdown_content"]
                baseline = base_content
                if current is None:
                    # Generated metadata can change while a first edit is in progress.
                    marker = "## 转写原文\n\n"
                    current = markdown(self.get(sid)).split(marker, 1)[-1]
                    baseline = baseline.split(marker, 1)[-1]
                if not current.startswith(baseline):
                    raise ValueError("文稿已被修改，请保留草稿并重新打开记录后再保存。")
                content = append_markdown(content, current[len(baseline):])
            return db.execute(
                "UPDATE sessions SET markdown_content=?,title=COALESCE(?,title) WHERE id=?",
                (content, markdown_title(content), sid),
            ).rowcount

    def apply_polish(self, sid, content, base_content, revision_id=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if row is None:
                return False
            if row["status"] not in {"completed", "failed", "interrupted"}:
                raise ValueError("请先结束这条记录的转写，再应用或撤销润色。")
            session = dict(row)
            session["segments"] = [dict(segment) for segment in db.execute(
                "SELECT * FROM segments WHERE session_id=? ORDER BY start,id", (sid,),
            )]
            current = markdown(session)
            if current != base_content:
                raise ValueError("文稿已被修改，请保留润色预览并重新生成，避免覆盖新内容。")
            if revision_id is None:
                if content == current:
                    return True
                db.execute(
                    "INSERT INTO markdown_polishes(session_id,original_content,result_content,created_at) "
                    "VALUES(?,?,?,?)", (sid, current, content, datetime.now(timezone.utc).isoformat()),
                )
            else:
                revision = db.execute(
                    "SELECT * FROM markdown_polishes WHERE id=? AND session_id=? AND reverted_at IS NULL",
                    (revision_id, sid),
                ).fetchone()
                if revision is None or revision["result_content"] != current:
                    raise ValueError("这次润色已不能直接撤销，当前文稿未改动。")
                content = revision["original_content"]
                db.execute("UPDATE markdown_polishes SET reverted_at=? WHERE id=?",
                           (datetime.now(timezone.utc).isoformat(), revision_id))
            db.execute("UPDATE sessions SET markdown_content=?,title=COALESCE(?,title) WHERE id=?",
                       (content, markdown_title(content), sid))
            return True

    def delete(self, sid):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone() is None:
                return False
            db.execute("DELETE FROM segments WHERE session_id=?", (sid,))
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))
        directory = self.root / sid
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
        return True


def markdown_title(content):
    """Use the document's opening H1 as its name; leave unnamed drafts unchanged."""
    for line in (content or "").splitlines():
        if not line.strip():
            continue
        heading = re.fullmatch(r" {0,3}#[ \t]+(.+)", line)
        if heading:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", heading[1]).strip()
            if title and title.strip("#"):
                return title[:120]
        break
    return None


def timestamp(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def append_markdown(content, added):
    if not added:
        return content
    separator = "" if not content or content.endswith("\n") or added.startswith("\n") else "\n"
    return content + separator + added


def remove_photo_markdown(content, filename):
    pattern = re.compile(
        rf"(?:\n[ \t]*)*!\[[^\]]*\]\(photo/{re.escape(filename)}\)(?:[ \t]*\n)*"
    )
    updated = pattern.sub("\n\n", content)
    updated = re.sub(r"\n{3,}", "\n\n", updated)
    if content.endswith("\n") and not updated.endswith("\n"):
        updated += "\n"
    return updated


def replace_photo_markdown(content, old_filename, new_filename, text):
    pattern = re.compile(rf"!\[[^\]]*\]\(photo/{re.escape(old_filename)}\)")
    if pattern.search(content):
        return pattern.sub(text, content, count=1)
    return append_markdown(content, "\n\n" + text + "\n\n")


def segment_markdown(segment):
    text = "\n".join(line.strip() for line in segment["text"].replace("\r\n", "\n")
                     .replace("\r", "\n").split("\n") if line.strip())
    if segment.get("kind") == "image" and text:
        return "\n" + text + "\n"
    return ("（待校对）" if segment["review"] else "") + text if text else ""


def markdown(session):
    if session.get("markdown_content") is not None:
        return session["markdown_content"]
    title = session["title"].replace("\n", " ").replace("\r", " ")
    lines = [
        f"# {title}",
        "",
        f"- 创建时间：{format_created_at(session['created_at'])}",
        f"- 识别模型：Whisper {session['model']}",
        f"- 音频时长：{timestamp(session['duration'])}",
        "",
        "## 转写原文",
        "",
    ]
    for segment in session["segments"]:
        text = segment_markdown(segment)
        if text:
            lines.append(text)
    return "\n".join(lines)

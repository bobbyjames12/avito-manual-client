from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def data_directory():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AvitoManualClient"


class Store:
    def __init__(self, root=None):
        self.root = Path(root) if root else data_directory()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "photos").mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / "client.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS ads (
                id TEXT PRIMARY KEY, draft TEXT NOT NULL, queued TEXT, sent TEXT,
                updated TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, created TEXT NOT NULL, status TEXT NOT NULL,
                snapshot TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
            );
        """)

    def close(self):
        self.db.close()

    def get_setting(self, key, default=""):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def setting(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))

    def save(self, ad):
        with self.db:
            self.db.execute("""INSERT INTO ads(id,draft,updated) VALUES(?,?,?)
                ON CONFLICT(id) DO UPDATE SET draft=excluded.draft, updated=excluded.updated""",
                (ad["id"], json.dumps(ad, ensure_ascii=False), timestamp()))

    def get(self, ad_id):
        row = self.db.execute("SELECT * FROM ads WHERE id=?", (ad_id,)).fetchone()
        if not row:
            return None
        return {**dict(row), **{k: json.loads(row[k]) if row[k] else None for k in ("draft", "queued", "sent")}}

    def all(self):
        return [self.get(row[0]) for row in self.db.execute("SELECT id FROM ads ORDER BY updated DESC")]

    def enqueue(self, ad):
        from .model import validate
        errors = validate(ad)
        if errors:
            raise ValueError("\n".join(errors))
        self.save(ad)
        with self.db:
            self.db.execute("UPDATE ads SET queued=draft WHERE id=?", (ad["id"],))

    def unqueue(self, ad_id):
        with self.db:
            self.db.execute("UPDATE ads SET queued=NULL WHERE id=?", (ad_id,))

    def delete_draft(self, ad_id):
        row = self.get(ad_id)
        if row and row["sent"]:
            raise ValueError("Отправленную карточку нельзя удалить из локальной базы: она входит в фид.")
        with self.db:
            self.db.execute("DELETE FROM ads WHERE id=?", (ad_id,))

    def import_photo(self, source):
        source = Path(source)
        data = source.read_bytes()
        # Content-addressed files stay immutable when a user replaces the original.
        name = hashlib.sha256(data).hexdigest() + source.suffix.lower()
        target = self.root / "photos" / name
        if not target.exists():
            target.write_bytes(data)
        return name

    def snapshot(self):
        return [row["queued"] or row["sent"] for row in self.all() if row["queued"] or row["sent"]]

    def begin_job(self, ads):
        job_id = uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO jobs(id,created,status,snapshot) VALUES(?,?,?,?)",
                (job_id, timestamp(), "sending", json.dumps(ads, ensure_ascii=False)))
        return job_id

    def complete_job(self, job_id, url):
        job = self.db.execute("SELECT snapshot FROM jobs WHERE id=?", (job_id,)).fetchone()
        with self.db:
            for ad in json.loads(job[0]):
                row = self.get(ad["id"])
                self.db.execute("UPDATE ads SET sent=? WHERE id=?",
                                (json.dumps(ad, ensure_ascii=False), ad["id"]))
                if row["queued"] == ad:
                    self.db.execute("UPDATE ads SET queued=NULL WHERE id=?", (ad["id"],))
            self.db.execute("UPDATE jobs SET status='sent', detail=? WHERE id=?", (url, job_id))

    def fail_job(self, job_id, detail):
        with self.db:
            self.db.execute("UPDATE jobs SET status='error', detail=? WHERE id=?", (detail, job_id))

    def recover_jobs(self):
        with self.db:
            self.db.execute("""UPDATE jobs SET status='unknown',
                detail='Приложение закрыто во время отправки. Результат не подтверждён; повторите отправку с теми же ID.'
                WHERE status='sending'""")

    def jobs(self):
        return [dict(row) for row in self.db.execute("SELECT id,created,status,detail FROM jobs ORDER BY created DESC LIMIT 100")]

    def backup(self, destination):
        import zipfile
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "client.sqlite3"
            copy = sqlite3.connect(db_path)
            self.db.backup(copy)
            copy.close()
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(db_path, "client.sqlite3")
                for photo in (self.root / "photos").iterdir():
                    archive.write(photo, "photos/" + photo.name)

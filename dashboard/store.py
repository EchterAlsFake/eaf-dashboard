from __future__ import annotations

import hashlib
import math
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS administrator (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    user_handle BLOB NOT NULL,
    session_generation INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webauthn_credential (
    credential_id BLOB PRIMARY KEY,
    public_key BLOB NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    transports TEXT NOT NULL DEFAULT 'usb',
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS bootstrap_token (
    token_hash BLOB PRIMARY KEY,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS login_challenge (
    id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,
    challenge BLOB NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    outcome TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visitor_seen (
    day TEXT NOT NULL,
    host TEXT NOT NULL,
    route_group TEXT NOT NULL,
    token_hash BLOB NOT NULL,
    PRIMARY KEY(day, host, route_group, token_hash)
);
CREATE TABLE IF NOT EXISTS daily_usage (
    day TEXT NOT NULL,
    host TEXT NOT NULL,
    route_group TEXT NOT NULL,
    metric TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(day, host, route_group, metric)
);
CREATE TABLE IF NOT EXISTS metric_snapshot (
    series TEXT PRIMARY KEY,
    value REAL NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS security_hourly (
    hour TEXT NOT NULL,
    event TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(hour, event)
);
"""


def utc_now() -> datetime:
    return datetime.now(UTC)


class Store:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            # Web and timer processes can start together. Serialize additive migrations so two
            # initializers cannot both observe a missing column and race the same ALTER TABLE.
            connection.execute("BEGIN IMMEDIATE")
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(administrator)")
            }
            if "session_generation" not in columns:
                connection.execute(
                    "ALTER TABLE administrator ADD COLUMN "
                    "session_generation INTEGER NOT NULL DEFAULT 1"
                )

    def administrator(self) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute("SELECT * FROM administrator WHERE id=1").fetchone()

    def set_password(self, password_hash: str) -> None:
        now = utc_now().isoformat()
        with self.connect() as connection:
            current = connection.execute(
                "SELECT user_handle,session_generation FROM administrator WHERE id=1"
            ).fetchone()
            handle = current[0] if current else secrets.token_bytes(32)
            generation = int(current[1]) + 1 if current else 1
            connection.execute(
                "INSERT INTO administrator"
                "(id,password_hash,user_handle,session_generation,created_at) "
                "VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "password_hash=excluded.password_hash,"
                "session_generation=excluded.session_generation",
                (password_hash, handle, generation, now),
            )

    def session_generation(self) -> int | None:
        administrator = self.administrator()
        return int(administrator["session_generation"]) if administrator else None

    def credentials(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM webauthn_credential ORDER BY created_at"
                )
            )

    def add_credential(
        self, credential_id: bytes, public_key: bytes, sign_count: int, label: str
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO webauthn_credential"
                "(credential_id,public_key,sign_count,label,created_at) VALUES(?,?,?,?,?)",
                (credential_id, public_key, sign_count, label[:80], utc_now().isoformat()),
            )

    def update_credential_counter(self, credential_id: bytes, sign_count: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE webauthn_credential SET sign_count=?,last_used_at=? WHERE credential_id=?",
                (sign_count, utc_now().isoformat(), credential_id),
            )

    def new_bootstrap_token(self, lifetime_minutes: int = 10) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).digest()
        expires = utc_now() + timedelta(minutes=lifetime_minutes)
        with self.connect() as connection:
            connection.execute("DELETE FROM bootstrap_token")
            connection.execute(
                "INSERT INTO bootstrap_token(token_hash,expires_at) VALUES(?,?)",
                (token_hash, expires.isoformat()),
            )
        return token

    def consume_bootstrap_token(self, token: str, *, mark: bool = False) -> bool:
        digest = hashlib.sha256(token.encode()).digest()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT expires_at,used_at FROM bootstrap_token WHERE token_hash=?",
                (digest,),
            ).fetchone()
            valid = bool(
                row
                and row["used_at"] is None
                and datetime.fromisoformat(row["expires_at"]) > utc_now()
            )
            if valid and mark:
                connection.execute(
                    "UPDATE bootstrap_token SET used_at=? WHERE token_hash=?",
                    (utc_now().isoformat(), digest),
                )
            return valid

    def put_challenge(self, purpose: str, challenge: bytes) -> str:
        challenge_id = secrets.token_urlsafe(24)
        expires = utc_now() + timedelta(minutes=3)
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM login_challenge WHERE expires_at < ?", (utc_now().isoformat(),)
            )
            connection.execute(
                "INSERT INTO login_challenge(id,purpose,challenge,expires_at) VALUES(?,?,?,?)",
                (challenge_id, purpose, challenge, expires.isoformat()),
            )
            connection.execute(
                "DELETE FROM login_challenge WHERE rowid NOT IN "
                "(SELECT rowid FROM login_challenge "
                "ORDER BY expires_at DESC,rowid DESC LIMIT 100)"
            )
        return challenge_id

    def take_challenge(self, challenge_id: str, purpose: str) -> bytes | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT challenge,expires_at FROM login_challenge WHERE id=? AND purpose=?",
                (challenge_id, purpose),
            ).fetchone()
            connection.execute("DELETE FROM login_challenge WHERE id=?", (challenge_id,))
            if not row or datetime.fromisoformat(row["expires_at"]) <= utc_now():
                return None
            return bytes(row["challenge"])

    def audit(
        self, action: str, resource_type: str, resource_id: str | None, outcome: str
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO audit_event(occurred_at,action,resource_type,resource_id,outcome) "
                "VALUES(?,?,?,?,?)",
                (utc_now().isoformat(), action, resource_type, resource_id, outcome),
            )

    def security_event(self, event: str) -> None:
        if event not in {
            "password_failure",
            "password_rate_limited",
            "webauthn_failure",
        }:
            raise ValueError("unsupported security event")
        now = utc_now()
        hour = now.replace(minute=0, second=0, microsecond=0).isoformat()
        cutoff = (now - timedelta(days=90)).isoformat()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO security_hourly(hour,event,value) VALUES(?,?,1) "
                "ON CONFLICT(hour,event) DO UPDATE SET value=value+1",
                (hour, event),
            )
            connection.execute("DELETE FROM security_hourly WHERE hour < ?", (cutoff,))

    def security_summary(self, hours: int = 24) -> dict[str, int]:
        window = max(1, min(hours, 24 * 90))
        cutoff = (utc_now() - timedelta(hours=window)).isoformat()
        with self.connect() as connection:
            return {
                row["event"]: int(row["value"])
                for row in connection.execute(
                    "SELECT event,sum(value) AS value FROM security_hourly "
                    "WHERE hour>=? GROUP BY event",
                    (cutoff,),
                )
            }

    def record_visit(self, host: str, route_group: str, token: str) -> None:
        day = utc_now().date().isoformat()
        token_hash = hashlib.sha256(f"{day}\0{token}".encode()).digest()[:16]
        with self.connect() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO visitor_seen(day,host,route_group,token_hash) VALUES(?,?,?,?)",
                (day, host, route_group, token_hash),
            ).rowcount
            if inserted:
                connection.execute(
                    "INSERT INTO daily_usage(day,host,route_group,metric,value) VALUES(?,?,?,?,1) "
                    "ON CONFLICT(day,host,route_group,metric) DO UPDATE SET value=value+1",
                    (day, host, route_group, "visitors"),
                )
            connection.execute(
                "DELETE FROM visitor_seen WHERE day < ?",
                ((utc_now().date() - timedelta(days=2)).isoformat(),),
            )

    def record_counter_delta(self, series: str, host: str, value: float) -> None:
        now = utc_now()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM metric_snapshot WHERE series=?", (series,)
            ).fetchone()
            previous = float(row[0]) if row else value
            delta = max(0, math.floor(value - previous))
            connection.execute(
                "INSERT INTO metric_snapshot(series,value,observed_at) VALUES(?,?,?) "
                "ON CONFLICT(series) DO UPDATE SET value=excluded.value,observed_at=excluded.observed_at",
                (series, value, now.isoformat()),
            )
            if delta:
                connection.execute(
                    "INSERT INTO daily_usage(day,host,route_group,metric,value) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(day,host,route_group,metric) DO UPDATE SET value=value+excluded.value",
                    (now.date().isoformat(), host, "api", "requests", delta),
                )

    def usage(self, days: int = 30) -> list[dict]:
        window = max(1, min(days, 7300))
        cutoff = (utc_now().date() - timedelta(days=window - 1)).isoformat()
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT day,host,route_group,metric,value FROM daily_usage "
                    "WHERE day>=? ORDER BY day,host,route_group,metric",
                    (cutoff,),
                )
            ]

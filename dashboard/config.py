from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_path: Path
    secret_key: str
    rp_id: str
    origin: str
    broker_socket: Path
    secure_cookie: bool

    @classmethod
    def load(cls) -> "Settings":
        data_dir = Path(os.environ.get("EAF_DASHBOARD_DATA_DIR", "./data")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        secret_file = data_dir / ".secret-key"
        if secret_file.exists():
            secret_key = secret_file.read_text(encoding="utf-8").strip()
        else:
            secret_key = os.urandom(32).hex()
            fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(secret_key + "\n")
        if len(secret_key) < 32:
            raise RuntimeError("Dashboard secret key is invalid")
        rp_id = os.environ.get("EAF_DASHBOARD_RP_ID", "echteralsfake.me").strip().lower()
        origin = os.environ.get(
            "EAF_DASHBOARD_ORIGIN", "https://echteralsfake.me"
        ).strip().rstrip("/")
        return cls(
            data_dir=data_dir,
            database_path=data_dir / "dashboard.db",
            secret_key=secret_key,
            rp_id=rp_id,
            origin=origin,
            broker_socket=Path(
                os.environ.get("EAF_DASHBOARD_BROKER_SOCKET", "/run/eaf-dashboard/broker.sock")
            ),
            secure_cookie=os.environ.get("EAF_DASHBOARD_SECURE_COOKIE", "true").lower()
            not in {"0", "false", "no"},
        )

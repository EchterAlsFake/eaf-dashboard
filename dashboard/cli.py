from __future__ import annotations

import argparse
import getpass
import os

from .auth import PASSWORD_HASHER
from .broker_client import call as broker_call
from .config import Settings
from .store import Store


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the local dashboard administrator")
    parser.add_argument("command", choices=("initialize", "enroll", "reset", "metrics"))
    args = parser.parse_args()
    settings = Settings.load()
    store = Store(settings.database_path)
    store.initialize()
    if args.command == "metrics":
        for item in broker_call(settings.broker_socket, "caddy_metrics"):
            store.record_counter_delta(item["series"], item["host"], item["value"])
        return 0
    if args.command in {"initialize", "reset"}:
        first = getpass.getpass("New dashboard password: ")
        second = getpass.getpass("Repeat dashboard password: ")
        if first != second or len(first) < 16:
            parser.error("Passwords must match and contain at least 16 characters")
        store.set_password(PASSWORD_HASHER.hash(first))
        if args.command == "reset":
            with store.connect() as connection:
                connection.execute("DELETE FROM webauthn_credential")
                connection.execute("DELETE FROM login_challenge")
        os.chmod(settings.database_path, 0o600)
    token = store.new_bootstrap_token()
    print(f"{settings.origin}/dashboard/enroll/{token}")
    print("This single-use enrollment URL expires in 10 minutes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

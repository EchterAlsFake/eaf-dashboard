from __future__ import annotations

import json
import socket
from pathlib import Path


class BrokerError(RuntimeError):
    pass


def call(socket_path: Path, operation: str, **arguments):
    request = json.dumps({"operation": operation, "arguments": arguments}, separators=(",", ":"))
    if len(request) > 65536:
        raise BrokerError("broker request too large")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(15)
            client.connect(str(socket_path))
            client.sendall(request.encode() + b"\n")
            chunks = bytearray()
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.extend(chunk)
                if len(chunks) > 4 * 1024 * 1024:
                    raise BrokerError("broker response too large")
                if b"\n" in chunk:
                    break
    except (OSError, TimeoutError) as exc:
        raise BrokerError("broker unavailable") from exc
    try:
        response = json.loads(bytes(chunks).split(b"\n", 1)[0])
    except (ValueError, UnicodeDecodeError) as exc:
        raise BrokerError("invalid broker response") from exc
    if not response.get("ok"):
        raise BrokerError(str(response.get("error", "broker operation failed")))
    return response.get("result")

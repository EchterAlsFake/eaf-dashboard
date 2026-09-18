from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import threading
import time
from functools import wraps

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask import abort, jsonify, request, session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .store import Store, utc_now


PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


class LoginLimiter:
    def __init__(self, secret: str):
        self.secret = secret.encode()
        self.entries: dict[str, tuple[int, float, float]] = {}
        self.lock = threading.Lock()

    def _keys(self) -> tuple[str, str]:
        address = request.remote_addr or "unknown"
        if address in {"127.0.0.1", "::1"}:
            candidate = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            try:
                address = str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
        hour = int(time.time() // 3600)
        client = hmac.new(
            self.secret, f"client:{hour}:{address}".encode(), hashlib.sha256
        ).hexdigest()
        global_bucket = hmac.new(
            self.secret, f"global:{hour}".encode(), hashlib.sha256
        ).hexdigest()
        return client, global_bucket

    def _prune(self, now: float) -> None:
        stale = [key for key, (_, _, seen) in self.entries.items() if now - seen > 7200]
        for key in stale:
            self.entries.pop(key, None)

    def allow(self) -> bool:
        now = time.monotonic()
        client_key, global_key = self._keys()
        with self.lock:
            self._prune(now)
            if client_key not in self.entries and len(self.entries) >= 4096:
                return False
            for key, maximum in ((client_key, 20), (global_key, 500)):
                failures, blocked_until, _ = self.entries.get(key, (0, 0.0, now))
                if blocked_until > now or failures >= maximum:
                    return False
            return True

    def fail(self) -> None:
        keys = self._keys()
        with self.lock:
            self._prune(time.monotonic())
            if keys[0] not in self.entries and len(self.entries) >= 4096:
                return
            for index, key in enumerate(keys):
                failures, _, _ = self.entries.get(key, (0, 0.0, time.monotonic()))
                failures += 1
                delay = 0 if index or failures < 5 else min(60, 2 ** (failures - 5))
                now = time.monotonic()
                self.entries[key] = (failures, now + delay, now)

    def success(self) -> None:
        with self.lock:
            self.entries.pop(self._keys()[0], None)


def encode_options(options) -> dict:
    return json.loads(options_to_json(options))


def install_auth(app, store: Store, settings) -> None:
    limiter = LoginLimiter(settings.secret_key)

    def json_object() -> dict:
        if not request.is_json:
            abort(415)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400)
        return payload

    def logged_in() -> bool:
        authenticated = session.get("authenticated_at")
        active = session.get("last_active_at")
        now = int(time.time())
        generation = store.session_generation()
        if (
            not isinstance(authenticated, int)
            or not isinstance(active, int)
            or generation is None
            or session.get("auth_generation") != generation
        ):
            session.clear()
            return False
        if now - active > 900 or now - authenticated > 28800:
            session.clear()
            return False
        session["last_active_at"] = now
        return True

    def require_auth(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not logged_in():
                return jsonify({"error": "authentication_required"}), 401
            return view(*args, **kwargs)

        return wrapped

    app.extensions["dashboard_logged_in"] = logged_in
    app.extensions["dashboard_require_auth"] = require_auth

    @app.post("/dashboard/api/auth/password")
    def password_login():
        if not limiter.allow():
            store.security_event("password_rate_limited")
            return jsonify({"error": "try_later"}), 429
        payload = json_object()
        password = payload.get("password")
        administrator = store.administrator()
        if (
            not administrator
            or not isinstance(password, str)
            or not password
            or len(password) > 1024
        ):
            limiter.fail()
            store.security_event("password_failure")
            return jsonify({"error": "invalid_credentials"}), 401
        try:
            PASSWORD_HASHER.verify(administrator["password_hash"], password)
        except VerifyMismatchError:
            limiter.fail()
            store.security_event("password_failure")
            return jsonify({"error": "invalid_credentials"}), 401
        limiter.success()
        if not session.get("authenticated_at"):
            session.clear()
        session["password_at"] = int(time.time())
        return jsonify({"ok": True})

    def authentication_options(purpose: str):
        password_at = session.get("password_at")
        if not isinstance(password_at, int) or int(time.time()) - password_at > 180:
            return None, (jsonify({"error": "password_required"}), 401)
        credentials = store.credentials()
        if not credentials:
            return None, (jsonify({"error": "no_credential_enrolled"}), 503)
        options = generate_authentication_options(
            rp_id=settings.rp_id,
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=bytes(row["credential_id"]))
                for row in credentials
            ],
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        challenge_id = store.put_challenge(purpose, options.challenge)
        response = encode_options(options)
        response["challengeId"] = challenge_id
        return response, None

    @app.post("/dashboard/api/auth/options")
    def auth_options():
        result, error = authentication_options("login")
        return error or jsonify(result)

    def verify_assertion(purpose: str):
        payload = json_object()
        challenge_id = payload.get("challengeId")
        credential = payload.get("credential")
        if not isinstance(challenge_id, str) or not isinstance(credential, dict):
            limiter.fail()
            store.security_event("webauthn_failure")
            return None, (jsonify({"error": "invalid_request"}), 400)
        challenge = store.take_challenge(challenge_id, purpose)
        if challenge is None:
            limiter.fail()
            store.security_event("webauthn_failure")
            return None, (jsonify({"error": "challenge_expired"}), 400)
        try:
            credential_id = base64.urlsafe_b64decode(credential["rawId"] + "===")
        except (KeyError, TypeError, ValueError):
            limiter.fail()
            store.security_event("webauthn_failure")
            return None, (jsonify({"error": "invalid_credential"}), 400)
        row = next(
            (item for item in store.credentials() if bytes(item["credential_id"]) == credential_id),
            None,
        )
        if row is None:
            limiter.fail()
            store.security_event("webauthn_failure")
            return None, (jsonify({"error": "unknown_credential"}), 401)
        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=settings.rp_id,
                expected_origin=settings.origin,
                credential_public_key=bytes(row["public_key"]),
                credential_current_sign_count=int(row["sign_count"]),
                require_user_verification=False,
            )
        except Exception:
            limiter.fail()
            store.security_event("webauthn_failure")
            return None, (jsonify({"error": "invalid_assertion"}), 401)
        limiter.success()
        store.update_credential_counter(credential_id, verified.new_sign_count)
        return verified, None

    @app.post("/dashboard/api/auth/verify")
    def auth_verify():
        _, error = verify_assertion("login")
        if error:
            return error
        now = int(time.time())
        session.clear()
        session["authenticated_at"] = now
        session["last_active_at"] = now
        session["step_up_at"] = now
        session["auth_generation"] = store.session_generation()
        session["csrf_token"] = secrets.token_urlsafe(24)
        store.audit("login", "session", None, "success")
        response = jsonify({"ok": True})
        response.set_cookie(
            "eaf_dashboard_csrf",
            session["csrf_token"],
            secure=settings.secure_cookie,
            httponly=False,
            samesite="Strict",
            path="/dashboard",
        )
        return response

    @app.post("/dashboard/api/auth/step-up/options")
    @require_auth
    def step_up_options():
        result, error = authentication_options("step-up")
        return error or jsonify(result)

    @app.post("/dashboard/api/auth/step-up/verify")
    @require_auth
    def step_up_verify():
        _, error = verify_assertion("step-up")
        if error:
            return error
        session["step_up_at"] = int(time.time())
        return jsonify({"ok": True})

    @app.get("/dashboard/api/auth/check")
    def auth_check():
        if not logged_in():
            return "", 401
        return "", 204

    @app.get("/dashboard/api/auth/status")
    def auth_status():
        authenticated = logged_in()
        step_up = session.get("step_up_at")
        return jsonify(
            {
                "authenticated": authenticated,
                "stepUpFresh": bool(
                    authenticated
                    and isinstance(step_up, int)
                    and int(time.time()) - step_up <= 300
                ),
            }
        )

    @app.post("/dashboard/api/auth/logout")
    def logout():
        if logged_in():
            store.audit("logout", "session", None, "success")
        session.clear()
        response = jsonify({"ok": True})
        response.delete_cookie("eaf_dashboard_csrf", path="/dashboard")
        return response

    @app.post("/dashboard/api/enroll/<token>/options")
    def enroll_options(token: str):
        if not store.consume_bootstrap_token(token):
            abort(404)
        administrator = store.administrator()
        if not administrator:
            return jsonify({"error": "administrator_not_initialized"}), 503
        existing = store.credentials()
        options = generate_registration_options(
            rp_id=settings.rp_id,
            rp_name="EchterAlsFake Administration",
            user_name="administrator",
            user_display_name="Administrator",
            user_id=bytes(administrator["user_handle"]),
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=bytes(row["credential_id"]))
                for row in existing
            ],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.DISCOURAGED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
        )
        challenge_id = store.put_challenge("enroll", options.challenge)
        response = encode_options(options)
        response["challengeId"] = challenge_id
        return jsonify(response)

    @app.post("/dashboard/api/enroll/<token>/verify")
    def enroll_verify(token: str):
        if not store.consume_bootstrap_token(token):
            abort(404)
        payload = json_object()
        challenge = store.take_challenge(str(payload.get("challengeId", "")), "enroll")
        if challenge is None:
            return jsonify({"error": "challenge_expired"}), 400
        try:
            verified = verify_registration_response(
                credential=payload["credential"],
                expected_challenge=challenge,
                expected_rp_id=settings.rp_id,
                expected_origin=settings.origin,
                require_user_verification=False,
            )
            store.add_credential(
                verified.credential_id,
                verified.credential_public_key,
                verified.sign_count,
                str(payload.get("label") or "Flipper Zero U2F"),
            )
        except Exception:
            return jsonify({"error": "registration_failed"}), 400
        store.consume_bootstrap_token(token, mark=True)
        store.audit("enroll", "credential", None, "success")
        return jsonify({"ok": True})


def require_step_up() -> None:
    step_up = session.get("step_up_at")
    if not isinstance(step_up, int) or int(time.time()) - step_up > 300:
        abort(428, description="fresh_authentication_required")

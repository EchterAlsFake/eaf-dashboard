from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from datetime import UTC, datetime
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, render_template, request, session

from .auth import install_auth, require_step_up
from .broker_client import BrokerError, call as broker_call
from .config import Settings
from .store import Store


TRACKED_HOSTS = {
    "echteralsfake.me",
    "docs.echteralsfake.me",
    "vplan.echteralsfake.me",
}
def create_app(settings: Settings | None = None) -> Flask:
    active = settings or Settings.load()
    origin_host = urlsplit(active.origin).hostname
    if not origin_host:
        raise RuntimeError("Dashboard origin is invalid")
    app = Flask(
        __name__,
        static_folder="static",
        static_url_path="/dashboard/static",
        template_folder="templates",
    )
    app.config.update(
        SECRET_KEY=active.secret_key,
        SESSION_COOKIE_NAME="eaf_dashboard_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=active.secure_cookie,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_PATH="/dashboard",
        MAX_CONTENT_LENGTH=64 * 1024,
        MAX_FORM_MEMORY_SIZE=16 * 1024,
        MAX_FORM_PARTS=10,
        TRUSTED_HOSTS=sorted(TRACKED_HOSTS | {origin_host, "localhost", "127.0.0.1", "[::1]"}),
    )
    store = Store(active.database_path)
    store.initialize()
    app.extensions["dashboard_settings"] = active
    install_auth(app, store, active)
    require_auth = app.extensions["dashboard_require_auth"]

    @app.before_request
    def protect_mutations():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        origin = request.headers.get("Origin")
        if request.path.startswith("/dashboard/") and (
            origin is None or not hmac.compare_digest(origin, active.origin)
        ):
            abort(403)
        if request.path.startswith("/dashboard/") and session.get("authenticated_at"):
            expected = session.get("csrf_token")
            provided = request.headers.get("X-EAF-CSRF")
            if not expected or not provided or not secrets.compare_digest(expected, provided):
                abort(403)
        return None

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Origin-Agent-Cluster"] = "?1"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
            "publickey-credentials-get=(self), publickey-credentials-create=(self)"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; frame-src 'none'; worker-src 'none'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.path.startswith("/dashboard"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(428)
    def reauthentication_required(_error):
        return jsonify({"error": "fresh_authentication_required"}), 428

    def api_http_error(error):
        if request.path.startswith("/dashboard/api/"):
            names = {
                400: "invalid_request",
                403: "forbidden",
                404: "not_found",
                405: "method_not_allowed",
                413: "request_too_large",
                415: "json_required",
                429: "try_later",
            }
            return jsonify({"error": names.get(error.code, "request_failed")}), error.code
        return error

    for status_code in (400, 403, 404, 405, 413, 415, 429):
        app.register_error_handler(status_code, api_http_error)

    @app.get("/dashboard/healthz")
    def health():
        return jsonify({"ok": True, "configured": store.administrator() is not None})

    @app.get("/dashboard/login")
    def login_page():
        return render_template("login.html", configured=store.administrator() is not None)

    @app.get("/dashboard/enroll/<token>")
    def enroll_page(token: str):
        if not store.consume_bootstrap_token(token):
            abort(404)
        return render_template("enroll.html", token=token)

    @app.get("/dashboard")
    @app.get("/dashboard/")
    def dashboard_page():
        if not app.extensions["dashboard_logged_in"]():
            return render_template("login.html", configured=store.administrator() is not None), 401
        return render_template("dashboard.html")

    @app.get("/dashboard/api/overview")
    @require_auth
    def overview():
        try:
            snapshot = broker_call(active.broker_socket, "overview")
        except BrokerError as exc:
            app.logger.error("Dashboard overview broker failure: %s", exc)
            snapshot = {"error": "data_source_unavailable", "services": []}
        snapshot["usage"] = store.usage(30)
        snapshot["security"] = store.security_summary(24)
        return jsonify(snapshot)

    @app.get("/dashboard/api/usage")
    @require_auth
    def usage():
        raw_days = request.args.get("days", "30")
        if not raw_days.isascii() or not raw_days.isdecimal():
            return jsonify({"error": "invalid_days"}), 400
        days = int(raw_days)
        if not 1 <= days <= 7300:
            return jsonify({"error": "invalid_days"}), 400
        return jsonify({"items": store.usage(days)})

    @app.get("/dashboard/api/revenue")
    @require_auth
    def revenue():
        try:
            return jsonify(broker_call(active.broker_socket, "revenue"))
        except BrokerError as exc:
            app.logger.error("Dashboard revenue broker failure: %s", exc)
            return jsonify({"error": "data_source_unavailable"}), 503

    @app.get("/dashboard/api/<kind>")
    @require_auth
    def records(kind: str):
        operations = {
            "errors": "list_errors",
            "feedback": "list_feedback",
            "payments": "list_payments",
            "licenses": "list_licenses",
            "checklist": "list_checklist",
            "audit": None,
        }
        if kind not in operations:
            abort(404)
        if kind == "audit":
            with store.connect() as connection:
                items = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT * FROM audit_event ORDER BY id DESC LIMIT 200"
                    )
                ]
            return jsonify({"items": items})
        try:
            return jsonify({"items": broker_call(active.broker_socket, operations[kind])})
        except BrokerError as exc:
            app.logger.error("Dashboard records broker failure for %s: %s", kind, exc)
            return jsonify({"error": "data_source_unavailable"}), 503

    @app.post("/dashboard/api/actions/<action>")
    @require_auth
    def action(action: str):
        allowed = {
            "delete_error": ("delete_error", "error"),
            "delete_feedback": ("delete_feedback", "feedback"),
            "toggle_checklist": ("toggle_checklist", "checklist"),
            "delete_checklist": ("delete_checklist", "checklist"),
            "restart_service": ("restart_service", "service"),
            "backup": ("backup", "backup"),
        }
        if action not in allowed:
            abort(404)
        require_step_up()
        if not request.is_json:
            abort(415)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400)
        confirmation = payload.pop("confirmation", None)
        resource_id = str(payload.get("id") or payload.get("service") or action)
        if confirmation != resource_id:
            return jsonify({"error": "confirmation_mismatch"}), 400
        operation, resource_type = allowed[action]
        try:
            result = broker_call(active.broker_socket, operation, **payload)
        except BrokerError as exc:
            store.audit(action, resource_type, resource_id, "failure")
            app.logger.error("Dashboard action broker failure for %s: %s", action, exc)
            return jsonify({"error": "operation_failed"}), 503
        store.audit(action, resource_type, resource_id, "success")
        return jsonify({"ok": True, "result": result})

    @app.post("/__eaf/visit")
    def visit():
        host = request.host.split(":", 1)[0].lower()
        payload = request.get_json(silent=True)
        if (
            not request.is_json
            or not isinstance(payload, dict)
            or host not in TRACKED_HOSTS
            or request.headers.get("Origin", "") != f"https://{host}"
        ):
            abort(404)
        address = request.remote_addr or "unknown"
        if address in {"127.0.0.1", "::1"}:
            forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            try:
                address = str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        day = datetime.now(UTC).date().isoformat()
        daily_key = hmac.new(
            active.secret_key.encode(), f"visit\0{day}\0{address}".encode(), hashlib.sha256
        ).hexdigest()
        # Route names are intentionally not client-controlled. Endpoint-level charts only need
        # the host, and a fixed group prevents unbounded attacker-selected SQLite cardinality.
        store.record_visit(host, "page", daily_key)
        return "", 204

    return app


app = create_app()

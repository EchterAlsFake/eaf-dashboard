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


LEGACY_TRACKED_HOSTS = {
    "echteralsfake.me",
    "docs.echteralsfake.me",
    "vplan.echteralsfake.me",
}


def tracked_hosts(rp_id: str, origin_host: str) -> set[str]:
    """Return the bounded analytics allowlist for this deployment.

    The production domain is configured at runtime.  Keeping only the original
    deployment's names here caused the visit endpoint to reject every request
    after a domain migration.
    """
    hosts = set(LEGACY_TRACKED_HOSTS)
    for host in (rp_id, origin_host):
        normalized = host.strip().lower().rstrip(".")
        if normalized:
            hosts.add(normalized)
            hosts.add(f"www.{normalized}")
    return hosts


def create_app(settings: Settings | None = None) -> Flask:
    active = settings or Settings.load()
    origin_host = urlsplit(active.origin).hostname
    if not origin_host:
        raise RuntimeError("Dashboard origin is invalid")
    analytics_hosts = tracked_hosts(active.rp_id, origin_host)
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
        TRUSTED_HOSTS=sorted(analytics_hosts | {"localhost", "127.0.0.1", "[::1]"}),
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
        if request.path in {"/dashboard/api/nodes/heartbeat", "/dashboard/api/embed/ingest"}:
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
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Origin-Agent-Cluster"] = "?1"
        if (
            request.path.startswith("/dashboard/embed/")
            or request.path == "/dashboard/static/embed.js"
            or request.path.startswith("/dashboard/api/embed/")
        ):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src *; "
                "frame-ancestors *; base-uri 'none'"
            )
            response.headers.pop("X-Frame-Options", None)
            response.headers.pop("Cross-Origin-Opener-Policy", None)
            response.headers.pop("Cross-Origin-Embedder-Policy", None)
            response.headers.pop("Cross-Origin-Resource-Policy", None)
        else:
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
            response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
            response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
            response.headers["Permissions-Policy"] = (
                "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
                "publickey-credentials-get=(self), publickey-credentials-create=(self)"
            )
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "connect-src 'self'; object-src 'none'; frame-src 'none'; worker-src 'none'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        if request.path.startswith("/dashboard") and not request.path.startswith("/dashboard/static"):
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
        snapshot["nodes"] = store.list_nodes_with_telemetry()
        try:
            snapshot["lan_devices"] = broker_call(active.broker_socket, "scan_lan")
        except Exception:
            snapshot["lan_devices"] = []
        return jsonify(snapshot)

    @app.get("/dashboard/api/nodes")
    @require_auth
    def get_nodes():
        return jsonify({"items": store.list_nodes_with_telemetry()})

    @app.post("/dashboard/api/nodes")
    @require_auth
    def create_node():
        if not request.is_json:
            abort(415)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400)
        node_id = str(payload.get("id") or "").strip()
        name = str(payload.get("name") or "").strip()
        address = str(payload.get("address") or "").strip()
        if not node_id or not name or not address:
            return jsonify({"error": "id, name and address are required"}), 400
        role = str(payload.get("role") or "server").strip()
        probe_url = str(payload.get("probe_url") or "").strip() or None
        auto_probe = bool(payload.get("auto_probe", True))
        node = store.upsert_node(
            id=node_id,
            name=name,
            address=address,
            role=role,
            probe_url=probe_url,
            auto_probe=auto_probe,
        )
        return jsonify({"ok": True, "node": node}), 201

    @app.post("/dashboard/api/nodes/<node_id>/probe")
    @require_auth
    def probe_node_endpoint(node_id: str):
        node = store.get_node(node_id)
        if not node:
            abort(404)
        probe_url = node.get("probe_url") or f"http://{node['address']}:9100/health"
        try:
            result = broker_call(active.broker_socket, "probe_node", url=probe_url)
        except BrokerError as exc:
            return jsonify({"error": str(exc)}), 502
        if result.get("ok"):
            store.record_node_telemetry(
                node_id=node_id,
                status="online",
                services=[{"name": "Probe", "url": probe_url, "status": "up", "latency_ms": result.get("latency_ms")}],
            )
        else:
            store.record_node_telemetry(
                node_id=node_id,
                status="offline",
                services=[{"name": "Probe", "url": probe_url, "status": "down", "error": result.get("error")}],
            )
        return jsonify({"ok": True, "result": result})

    @app.post("/dashboard/api/nodes/heartbeat")
    def node_heartbeat():
        if not request.is_json:
            abort(415)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            abort(400)
        node_id = str(payload.get("node_id") or payload.get("id") or "").strip()
        if not node_id:
            return jsonify({"error": "node_id is required"}), 400
        existing = store.get_node(node_id)
        address = str(payload.get("address") or request.remote_addr or "unknown")
        name = str(payload.get("name") or node_id)
        role = str(payload.get("role") or "server")
        if not existing:
            store.upsert_node(id=node_id, name=name, address=address, role=role)

        status = str(payload.get("status") or "online")
        cpu_percent = payload.get("cpu_percent")
        memory_used_mb = payload.get("memory_used_mb")
        memory_total_mb = payload.get("memory_total_mb")
        disk_used_gb = payload.get("disk_used_gb")
        disk_total_gb = payload.get("disk_total_gb")
        load_1m = payload.get("load_1m")
        uptime_seconds = payload.get("uptime_seconds")
        services = payload.get("services") if isinstance(payload.get("services"), list) else None
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else None

        store.record_node_telemetry(
            node_id=node_id,
            status=status,
            cpu_percent=cpu_percent,
            memory_used_mb=memory_used_mb,
            memory_total_mb=memory_total_mb,
            disk_used_gb=disk_used_gb,
            disk_total_gb=disk_total_gb,
            load_1m=load_1m,
            uptime_seconds=uptime_seconds,
            services=services,
            metrics=metrics,
        )
        return jsonify({"ok": True, "node_id": node_id, "timestamp": datetime.now(UTC).isoformat()})

    @app.route("/dashboard/api/embed/data", methods=["GET", "OPTIONS"])
    def embed_data():
        if request.method == "OPTIONS":
            resp = jsonify({})
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return resp, 204

        try:
            vitals = broker_call(active.broker_socket, "system_vitals")
        except Exception:
            vitals = {}

        nodes = store.list_nodes_with_telemetry()
        total_nodes = len(nodes)
        online_nodes = sum(1 for n in nodes if n.get("status") == "online")

        public_data = {
            "cluster": {
                "status": "operational" if (online_nodes == total_nodes and total_nodes > 0) else "degraded" if online_nodes > 0 else "offline",
                "nodes_total": total_nodes,
                "nodes_online": online_nodes,
            },
            "host": {
                "hostname": vitals.get("hostname", "msi-origin"),
                "status": "operational",
                "cpu_percent": vitals.get("cpu", {}).get("percent", 0),
                "memory_percent": vitals.get("memory", {}).get("percent", 0),
                "uptime_seconds": vitals.get("uptime_seconds", 0),
                "load": vitals.get("cpu", {}).get("load_1m", 0),
            },
            "nodes": [
                {
                    "id": n["id"],
                    "name": n["name"],
                    "role": n["role"],
                    "status": n.get("status", "unknown"),
                    "services": (n.get("telemetry") or {}).get("services", []),
                }
                for n in nodes
            ],
            "services": [
                {"name": "Web (pornfetch.to)", "status": "online"},
                {"name": "Licensing API", "status": "online"},
                {"name": "Error Relay", "status": "online"},
                {"name": "Rosenpass Post-Quantum VPN", "status": "online"},
            ],
            "timestamp": datetime.now(UTC).isoformat(),
        }
        resp = jsonify(public_data)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Cache-Control"] = "public, max-age=10"
        return resp

    @app.get("/dashboard/api/embed/nodes")
    def embed_nodes():
        nodes = store.list_nodes_with_telemetry()
        resp = jsonify({"items": nodes})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Cache-Control"] = "public, max-age=10"
        return resp

    @app.get("/dashboard/embed/view")
    def embed_view():
        widget_type = request.args.get("widget", "card")
        theme = request.args.get("theme", "dark")
        node_id = request.args.get("node", "all")
        return render_template("embed.html", widget=widget_type, theme=theme, node_id=node_id)

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
            "delete_node": (None, "node"),
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
        if action == "delete_node":
            deleted = store.delete_node(resource_id)
            if not deleted:
                store.audit(action, resource_type, resource_id, "failure")
                return jsonify({"error": "node_not_found_or_protected"}), 400
            store.audit(action, resource_type, resource_id, "success")
            return jsonify({"ok": True, "result": {"deleted": resource_id}})
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
            or host not in analytics_hosts
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

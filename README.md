# EAF Dashboard

EAF Dashboard is the private administration interface I use to see a compact
overview of several self-hosted services. It brings together aggregate traffic,
error reports, payment and licence summaries, a small operational checklist,
and service status in one place.

This repository is a reviewed public snapshot of the application. I publish it
to make the interface and its privacy choices understandable, and to keep the
reusable parts under version control. It is deliberately not a turnkey copy of
the production deployment.

## What is included

The repository contains:

- the Flask web application and browser interface;
- password and WebAuthn authentication;
- session, CSRF, step-up authentication, and rate-limit controls;
- the local SQLite schema for administrator state, audit events, and aggregate
  usage counters;
- privacy-preserving visitor counting that stores no raw IP address;
- a least-privileged host broker with an explicit operation and service allowlist;
- a multi-server federation engine and telemetry database for servers on the local network;
- embeddable Web Components, iframes, and CORS REST APIs for third-party websites;
- a zero-dependency Python 3 node telemetry agent (`dashboard/agent/eaf-node-agent.py`);
- the dependency definition and locked dependency graph;
- tests for the non-privileged application boundary.

For complete multi-server deployment and embedding instructions, see [MULTI-SERVER-FEDERATION.md](file:///home/asuna/analysis/eaf-dashboard/MULTI-SERVER-FEDERATION.md).

Authentication code is included intentionally. Security must rely on private
keys, random secrets, access control, and isolation rather than on hiding the
implementation. There are no real credentials or enrolled WebAuthn records in
this repository.

## What is not included

The public snapshot still omits secrets and host-specific recovery material:

- production systemd units and reverse-proxy configuration;
- the internal operational security review and recovery runbook;
- live databases, secret keys, enrollment links, passkeys, sessions, logs,
  backups, payment records, error reports, and environment files;
- the broker-specific revenue test, because it mirrors private integration
  details that are intentionally omitted.

The checked-in broker server implements the narrow local interface expected by
the web process. It must run as the unprivileged account that owns the services;
the web container should receive only a bind mount of its Unix socket. Database
paths, the backup directory, and logical-name-to-systemd-unit mappings are all
runtime configuration rather than browser-controlled values.

## Privacy model

The visitor counter uses a secret daily transform of the connecting address to
deduplicate visits. Short-lived duplicate-check values are removed after two
days; retained analytics contain only aggregate daily counts by host and broad
route group. It sets no analytics cookie and does not use an external analytics
service.

Operational records are shown through the private broker interface. The browser
receives only the fields needed for the selected dashboard view. Anyone adapting
the project should review those fields against their own privacy policy before
connecting a data source.

## Authentication model

Interactive access requires a local Argon2id password followed by WebAuthn.
Sessions have idle and absolute expiry, mutating requests require same-origin
CSRF protection, and privileged actions require recent step-up authentication
plus typed confirmation. Initial enrollment is performed from the server console
with a short-lived, single-use URL.

The defaults refer to the original deployment domain. Set
`EAF_DASHBOARD_RP_ID`, `EAF_DASHBOARD_ORIGIN`, `EAF_DASHBOARD_DATA_DIR`, and
`EAF_DASHBOARD_BROKER_SOCKET` for a different installation. WebAuthn origin and
RP-ID changes need careful review and a fresh credential enrollment.

## Development

Python 3.14 and `uv` are used by the current project:

```sh
uv sync --locked --dev
uv run pytest -q tests/test_dashboard.py
```

The web process can be exercised with a missing broker socket; broker-backed
views then fail closed with a generic data-source error. A real deployment must
place the application behind HTTPS, bind it to a private listener, run
`python -m dashboard.broker_server` as an unprivileged host service, and mount
only its socket directory at `/run/eaf-dashboard`. Run
`python -m dashboard.cli metrics` periodically to import the aggregate Caddy
counters. Caddy metrics need `metrics { per_host }` enabled; access logging is
not required.

## Security reports

Please do not publish a suspected vulnerability or production detail in a public
issue. Use GitHub's private vulnerability reporting for this repository. Do not
send tokens, database copies, passkey material, personal records, or live error
reports with a report.

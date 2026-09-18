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
- the dependency definition and locked dependency graph;
- tests for the non-privileged application boundary.

Authentication code is included intentionally. Security must rely on private
keys, random secrets, access control, and isolation rather than on hiding the
implementation. There are no real credentials or enrolled WebAuthn records in
this repository.

## What is not included

The public snapshot omits the parts that describe or control the live host in
too much detail:

- the root broker implementation and its database queries, service allowlist,
  backup commands, and host paths;
- production systemd units and reverse-proxy configuration;
- the internal operational security review and recovery runbook;
- live databases, secret keys, enrollment links, passkeys, sessions, logs,
  backups, payment records, error reports, and environment files;
- the broker-specific revenue test, because it mirrors private integration
  details that are intentionally omitted.

The checked-in broker client only defines the narrow local interface expected by
the web process. A deployment needs its own independently reviewed provider for
that interface. Copying this repository alone cannot restart services, read the
other applications' databases, or create production backups.

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
place the application behind HTTPS, bind it to a private listener, supply a
least-privileged broker, and add service-manager and reverse-proxy confinement
appropriate to its host.

## Security reports

Please do not publish a suspected vulnerability or production detail in a public
issue. Use GitHub's private vulnerability reporting for this repository. Do not
send tokens, database copies, passkey material, personal records, or live error
reports with a report.

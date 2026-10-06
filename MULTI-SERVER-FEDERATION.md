# Multi-Server Federation & Embedding Guide

This guide details how to connect multiple web servers and websites running on the same local network (router + Ethernet on `192.168.0.0/24`) to the central EAF Dashboard, and how to embed live status and metrics into external websites.

---

## 1. Architecture Overview

```
                         Router (192.168.0.1)
                                  │
         ┌────────────────────────┼────────────────────────┐
         │ (Ethernet)             │ (Ethernet)             │ (Ethernet)
         ▼                        ▼                        ▼
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ Central Server   │    │ Node Server 1    │    │ Node Server 2    │
│ (192.168.0.11)   │    │ (192.168.0.x)    │    │ (192.168.0.y)    │
│                  │    │                  │    │                  │
│ • EAF Dashboard  │◄───┤ • eaf-node-agent │◄───┤ • eaf-node-agent │
│   (:18010)       │    │ • Web App A      │    │ • Web App B      │
│ • Host Broker    │    │   (:80/443/3000) │    │   (:8080/etc)    │
│ • Quadlet / Podman│   └──────────────────┘    └──────────────────┘
└──────────────────┘
         ▲
         │ Embeds / REST API (CORS + iframe)
         │
┌──────────────────────────────────────┐
│ External Websites / Dashboards       │
│ • Shadow DOM Web Component           │
│ • Responsive iframe                  │
│ • Direct JSON REST API               │
└──────────────────────────────────────┘
```

The system operates across three tiers:
1. **Central Aggregator (Dashboard)**: Runs at `http://192.168.0.11:18010` (or your edge domain). Stores node definitions, historical and latest telemetry, and hosts the embed engine.
2. **Node Agent (`eaf-node-agent.py`)**: A zero-dependency Python 3 daemon deployed to each connected server. Collects host resource vitals (CPU, RAM, load, disk, uptime) and probes local web services, then pushes heartbeats to the central dashboard.
3. **Embed Engine**: Exposes CORS-enabled JSON endpoints, a Shadow DOM Web Component (`<eaf-widget>`), and iframe templates to embed status into any other website seamlessly.

---

## 2. Deploying Node Agents

Each server on the network runs the lightweight telemetry agent `eaf-node-agent.py`. It requires only Python 3.8+ and no third-party libraries (`requests`, etc. are NOT needed; it uses the standard library `urllib` and `http.server`).

### Quick Setup on a New Server

1. **Copy the agent script to the target server**:
   ```bash
   scp /home/asuna/staging/eaf-node-agent.py user@192.168.0.x:/usr/local/bin/eaf-node-agent
   chmod +x /usr/local/bin/eaf-node-agent
   ```

2. **Test metrics collection locally**:
   ```bash
   /usr/local/bin/eaf-node-agent --test
   ```

3. **Install and run as a systemd service**:
   ```bash
   sudo /usr/local/bin/eaf-node-agent \
     --dashboard-url http://192.168.0.11:18010 \
     --name "Media & Storage Server" \
     --role "storage" \
     --probe-service "Jellyfin=http://localhost:8096/health" \
     --probe-service "Nextcloud=http://localhost:8080/status.php" \
     --install-systemd
   ```

This automatically generates `/etc/systemd/system/eaf-node-agent.service`, reloads systemd, and starts the service.

### Agent Command-Line Flags

| Flag | Default | Description |
|---|---|---|
| `--dashboard-url` | `http://192.168.0.11:18010` | Central EAF Dashboard base URL |
| `--node-id` | `node-<hostname>` | Unique identifier for this node |
| `--name` | `<hostname>` | Friendly name displayed in the UI |
| `--address` | Auto-detected LAN IP | LAN IP address reachable by the central host |
| `--role` | `web-server` | Node role tag (`web-server`, `database`, `gateway`, `storage`, `worker`) |
| `--interval` | `15` | Heartbeat interval in seconds |
| `--serve-port` | `9100` | Optional local HTTP port serving `/health` (pull mode) |
| `--probe-service` | (None, repeatable) | Probes in `Name=URL` format, e.g. `--probe-service "Blog=http://localhost:3000"` |
| `--test` | - | Runs one collection pass, prints JSON to stdout, and exits |
| `--install-systemd` | - | Creates and enables systemd service on Linux |

---

## 3. Registering Nodes in the Dashboard

Nodes can be connected in two ways:

### Automatic Discovery via Heartbeat (Push Mode)
When `eaf-node-agent` sends its first heartbeat to `POST /dashboard/api/nodes/heartbeat`, the dashboard automatically upserts the node record and marks it online. No prior manual registration is required.

### Manual Registration via UI or API (Pull/Inventory Mode)
You can register nodes in advance from the **LAN Fleet & Servers** tab in the dashboard by clicking **"Register Node"**, or via the API:

```bash
curl -X POST http://192.168.0.11:18010/dashboard/api/nodes \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: <SESSION_CSRF_TOKEN>" \
  -d '{
    "node_id": "node-pi-dns",
    "name": "Raspberry Pi DNS",
    "address": "192.168.0.15",
    "role": "dns",
    "telemetry_url": "http://192.168.0.15:9100/health"
  }'
```

---

## 4. Embedding Status into Other Websites

The EAF Dashboard provides three ways for other websites (on the same network or across the internet) to display status and metrics.

### Method 1: Shadow-DOM Web Component (Recommended)

Include the `embed.js` script and place `<eaf-widget>` tags anywhere in your HTML. The widget uses Shadow DOM to ensure styles do not clash with the host page.

```html
<!-- 1. Include the embed library once in your page -->
<script src="http://192.168.0.11:18010/dashboard/static/embed.js" defer></script>

<!-- 2. Embed a compact status badge -->
<eaf-widget mode="badge" theme="dark"></eaf-widget>

<!-- 3. Embed a full host & cluster vitals card -->
<eaf-widget mode="card" theme="dark" refresh="15"></eaf-widget>

<!-- 4. Embed a live service availability list -->
<eaf-widget mode="services" theme="dark"></eaf-widget>

<!-- 5. Embed the multi-server LAN fleet overview -->
<eaf-widget mode="fleet" theme="dark"></eaf-widget>
```

#### Supported Web Component Attributes

| Attribute | Values | Default | Description |
|---|---|---|---|
| `mode` | `badge`, `card`, `services`, `fleet` | `card` | Display variant |
| `theme` | `dark`, `light` | `dark` | Color scheme |
| `src` | URL | (Current origin) | Base URL of dashboard (e.g. `http://192.168.0.11:18010`) |
| `refresh` | Number (seconds) | `20` | Auto-refresh interval (set `0` to disable) |

---

### Method 2: Responsive iframe Embed

For zero-JavaScript or CMS environments (WordPress, Notion, static markdown sites):

```html
<iframe
  src="http://192.168.0.11:18010/dashboard/embed/view?widget=card&theme=dark"
  width="100%"
  height="240"
  frameborder="0"
  style="border: none; border-radius: 12px; overflow: hidden;"
  loading="lazy">
</iframe>
```

Supported query parameters:
- `widget`: `badge`, `card`, `services`, `fleet`
- `theme`: `dark`, `light`

---

### Method 3: Direct JSON REST API (Headless Integration)

External frontends (Vue, React, Svelte, static site generators) can fetch live cluster metrics directly. The endpoints include `Access-Control-Allow-Origin: *` headers for direct client-side querying.

#### 1. Public Cluster Overview
`GET http://192.168.0.11:18010/dashboard/api/embed/data`

```json
{
  "cluster": {
    "nodes_online": 2,
    "nodes_total": 2,
    "status": "operational"
  },
  "host": {
    "cpu_percent": 11.2,
    "hostname": "archlinux",
    "load": 0.89,
    "memory_percent": 18.5,
    "status": "operational",
    "uptime_seconds": 134000
  },
  "services": [
    { "name": "Web (pornfetch.to)", "status": "online" },
    { "name": "Licensing API", "status": "online" },
    { "name": "Error Relay", "status": "online" },
    { "name": "Rosenpass Post-Quantum VPN", "status": "online" }
  ],
  "nodes": [
    {
      "id": "node-origin",
      "name": "MSI Stealth 15M (Origin Gateway)",
      "role": "gateway",
      "status": "online",
      "services": []
    }
  ],
  "timestamp": "2026-10-06T07:57:53Z"
}
```

#### 2. Detailed Multi-Node Telemetry
`GET http://192.168.0.11:18010/dashboard/api/embed/nodes`

Returns the complete array of registered nodes with individual CPU, memory, load, and probed sub-services.

---

## 5. Security & Network Isolation

1. **Firewall Boundary**:
   The central host runs `nftables` with a default input DROP policy. LAN access on port 18010 is explicitly restricted to the local subnet:
   ```nftables
   iifname "enp0s20f0u3c2" ip saddr 192.168.0.0/24 tcp dport 18010 accept
   ```
   No unauthenticated LAN traffic can reach internal broker sockets or unapproved ports.

2. **Public vs Private Boundaries**:
   - `/dashboard/api/embed/*` and `/dashboard/embed/view` only expose aggregate vitals and service status. They never leak secrets, database paths, passkey data, or environment credentials.
   - Heartbeats (`/dashboard/api/nodes/heartbeat`) accept telemetry payloads with validation and length bounds, without granting execution or proxy capabilities.
   - Administrative tasks (service restarts, backups, metric resets) remain strictly protected behind password + WebAuthn hardware passkeys and broker socket allowlists.

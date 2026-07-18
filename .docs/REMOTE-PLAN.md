# devbox — Remote Access Plan (Phase R)

How to run devbox containers on an always-on machine and **prompt them from
anywhere** — phone browser or laptop — without ever putting the devbox home
(`.devbox/`, App private key, secrets) on the client device.

> Status: **design only**. No code written yet. This complements
> `IMPLEMENTATION-PLAN.md` (the local build). Nothing here changes the existing
> security model; it adds a remote interaction surface on top of it.

---

## 1. Goals

- **Prompt from my phone.** Open a URL in a mobile browser and talk to opencode
  running inside a devbox container.
- **Prompt from my laptop.** Same session, via the opencode web UI, the opencode
  TUI, or Cursor "Attach to Running Container" — with no `.devbox/` locally.
- **Secrets never leave home base.** The App private key and `secrets/` live only
  on the always-on machine. Clients are thin.
- **Nothing exposed to the public internet.** Reachability is provided by a
  private mesh (Tailscale), not a public port.
- **A few concurrent sessions.** Run several repos at once, each with its own
  stable URL.
- **Smooth orchestration.** Spinning up a container should not require typing CLI
  commands on a phone keyboard.

---

## 2. Chosen setup (decisions made)

| Decision | Choice |
|----------|--------|
| **Home base** | A cloud VM (always-on), on the tailnet. Holds `.devbox/`, Docker, refresher, secrets. |
| **Transport** | Tailscale (WireGuard mesh) + Tailscale Serve for HTTPS. No public inbound. |
| **Clients** | Both phone browser and laptop (Cursor/TUI), equally supported. |
| **Concurrency** | A few repos at once → per-repo URLs via a small registry + path routing. |
| **Orchestration** | Target = thin control daemon (tap-a-repo web page). SSH is the day-one fallback. |

---

## 3. The three roles

Today all three run on one desktop. Remote use separates them; this plan keeps
**control plane + container host together on the VM**, and moves only the client.

1. **Control plane** — the devbox CLI/daemon. Holds the App private key +
   `secrets/`, mints tokens, runs the refresher, builds/starts containers.
   *Requires `.devbox/`. Lives on the VM only.*
2. **Container host** — the Docker engine running the containers (repo mount,
   opencode process). *Same machine as the control plane (the VM).*
3. **Client / prompting surface** — where you type. *Phone browser or laptop.
   Thin; needs no secrets and no `.devbox/`.*

**Why keep 1 and 2 together:** the token refresher writes `.run/<repo>/token` on
the host filesystem, and that dir is bind-mounted into the container so the
credential helper reads a live, rotating token. If Docker ran on a *different*
machine, that mount + refresher would have to live there anyway. Co-locating them
on the VM keeps the entire token model from `IMPLEMENTATION-PLAN.md` unchanged.

---

## 4. Topology

```
 Phone browser ───┐                        ┌──── Laptop (Cursor / opencode TUI)
                  │   Tailnet (WireGuard)   │
                  ▼                         ▼
        ┌───────────────────────────────────────────┐
        │  Cloud VM (home base, on your tailnet)      │
        │  • .devbox/ : CLI, App private key, secrets │
        │  • Docker engine + token refresher + .run/  │
        │  • Tailscale (+ Tailscale Serve for HTTPS)  │
        │  • (later) control daemon over the tailnet  │
        │                                             │
        │   container A: /workspace (repo A)          │
        │     └─ opencode web --hostname 0.0.0.0 :PA  │
        │   container B: /workspace (repo B)          │
        │     └─ opencode web --hostname 0.0.0.0 :PB  │
        └───────────────────────────────────────────┘
              inbound public: NOTHING (Tailscale only)
```

---

## 5. Interaction surface: opencode serve / web

opencode is designed for this:

- `opencode serve --hostname 0.0.0.0 --port <P>` — headless HTTP server (OpenAPI
  3.1), the backend for all clients.
- `opencode web --hostname 0.0.0.0 --port <P>` — headless server **plus a browser
  UI** (the phone path).
- `opencode attach http://<host>:<P>` — attach a TUI from another machine.
- Auth: `OPENCODE_SERVER_PASSWORD` (username defaults to `opencode`) enables HTTP
  basic auth. **Always set it** when binding beyond localhost.
- Sessions persist server-side → start on the phone, resume on the laptop.

**Client mapping**

- **Phone:** browser → `https://<vm>.<tailnet>.ts.net/<repo-path>/` (Tailscale
  Serve terminates HTTPS and proxies to the container's published port).
- **Laptop, opencode TUI:** `opencode attach https://<vm>.<tailnet>.ts.net/<repo-path>/`.
- **Laptop, Cursor:** a remote Docker context over Tailscale SSH lets Cursor's
  "Attach to Running Container" list and attach to the VM's containers
  transparently:
  `docker context create devboxvm --docker host=ssh://you@<vm>.ts.net`.

---

## 6. Networking: Tailscale

- Install Tailscale on the VM, phone, and laptop → one tailnet; `tailscale up`.
- **Lock down the VM:** close all public inbound. Tailscale traverses NAT
  outbound, so nothing needs to be open to the internet. Use **Tailscale SSH** and
  close public port 22.
- **Tailscale Serve** provides HTTPS with a valid cert on the MagicDNS name — no
  manual certs. For a few concurrent repos, route by **path**:
  `https://<vm>.<tailnet>.ts.net/<owner>-<repo>/ → localhost:<container-port>`.
- Restrict tailnet ACLs so only your own devices can reach the VM's serve ports.

---

## 7. Changes required in devbox (design)

Contained additions to the existing lifecycle in `IMPLEMENTATION-PLAN.md`.

### 7.1 Container lifecycle (`core/docker.py`, `commands/start.py`)
- **Serve step:** start `opencode web`/`serve` in the container bound to
  `0.0.0.0:<serverport>` (gated by mode exactly like today).
- **Port publishing:** `create_container` gains `-p <hostport>:<serverport>`.
  Allocate `<hostport>` **deterministically from `REPO_ID`** (stable per repo) with
  a fallback scan if taken; record it in the registry (§7.3).
- **Per-container password:** generate `OPENCODE_SERVER_PASSWORD` once per
  container, store in `.run/<repo>/opencode.pass` (gitignored, `chmod 600`),
  inject as env, and print it with the URL.
- **URL output:** `start` prints the tailnet URL + credentials instead of only
  the local attach hints.

### 7.2 Tailscale wiring (optional automation)
- `start` can invoke `tailscale serve` to map the per-repo path → published port,
  so the URL is turn-key. Otherwise document the one-time manual mapping.

### 7.3 Session registry (for "a few" concurrent)
- A small JSON registry under `.run/registry.json`: `repo → {container, hostport,
  path, mode, url}`. Powers `devbox list`, URL lookup, port allocation, and the
  control daemon. No heavyweight proxy needed at this scale — Tailscale Serve
  path routing is sufficient.

### 7.4 Control daemon (smooth orchestration — the phone target)
- A thin authenticated HTTP service on the VM, exposed **only over the tailnet**,
  wrapping `core/`:
  - `GET /repos` — known/initialized repos.
  - `POST /start {repo}` / `POST /stop {repo}` — lifecycle.
  - `GET /sessions` — running devboxes + their opencode URLs.
- Serves a minimal mobile web page: tap a repo → it starts (if needed) → hands you
  a link straight into that repo's opencode UI. This is what makes phone use
  "smooth" (no SSH, no typing commands).
- Keep it a thin layer over the same `core/` functions the CLI uses (already an
  explicit design goal in `IMPLEMENTATION-PLAN.md`).

### 7.5 Config additions
- Host-level `remote.env` (VM, gitignored): tailnet hostname, base port,
  Serve on/off, daemon bind/token.
- Per-repo `config.env`: none required; port/path derive from `REPO_ID` and live
  in the registry.

---

## 8. Bootstrapping the VM (new operational work)

1. Provision VM; install Docker, Python, `gh`, Tailscale; `tailscale up`.
2. Deploy `.devbox/` (git clone) **and** the non-git secrets — the App `.pem` and
   `secrets/ai.env`. Design this transfer carefully: scp over the tailnet or a
   secrets manager. **This is the main bootstrapping step to get right.**
3. `gh auth login` once on the VM (needed by `init` / branch-protection checks).
4. Thereafter: `devbox start <repo>` on the VM (via Tailscale SSH day one, or the
   control daemon once built) → receive the URL.

---

## 9. Security model additions

Everything in `IMPLEMENTATION-PLAN.md` §7 still holds. On top:

- **Private key on a rented VM:** enable full-disk encryption, restrict tailnet
  ACLs to your devices, close all public inbound, keep the VM patched. If ever
  compromised, rotating the App key invalidates all issued tokens.
- **The opencode endpoint is RCE + push/PR capability.** It must sit behind
  **both** `OPENCODE_SERVER_PASSWORD` **and** the tailnet. Never bind it to a
  public interface; never expose it without auth.
- **Branch protection remains the backstop** against merges to the default branch,
  regardless of who reaches the endpoint.
- **Control daemon** is tailnet-only + token-authenticated; it can start/stop
  containers, so treat it as privileged.

---

## 10. Phased roadmap

- **R0 — Serve locally over the tailnet:** opencode serve/web in the container +
  port publish + per-container password. Reach it from the laptop on the tailnet.
  (Proves the interaction path.)
- **R1 — Phone access:** Tailscale Serve HTTPS + phone browser. ← headline goal.
- **R2 — Laptop Cursor:** remote Docker context over Tailscale SSH; make
  `db-attach` context-aware.
- **R3 — Smooth orchestration:** session registry + control daemon (tap-a-repo web
  page) so containers spin up from the phone without SSH; path-routed URLs for a
  few concurrent repos.

---

## 11. Open questions / deferred

- **Cloud provider & sizing** for the VM (model inference is remote, so CPU needs
  are modest; RAM for builds + a few containers). To be chosen.
- **Secret transfer to the VM** — scp-over-tailnet vs a secrets manager.
- **Control-daemon auth** — shared token vs tailnet identity headers.
- **Scaling past "a few"** — a real reverse proxy + subdomain routing if session
  count grows (not needed now).
- **Splitting Docker onto a separate host** — possible later, but requires moving
  the refresher + `.run/` to that host; deferred.

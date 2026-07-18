# devbox — Implementation Plan

A portable command set for launching an isolated, credential-restricted Docker  
container to work on a git repository. The agent inside the container gets  
**full local git** but only **scoped remote access** (feature branches + pull  
requests) — never the ability to merge to the default branch, and never access  
to your personal credentials.

---

## 1. Goals & principles

- **One command to work safely in a repo.** `devbox start` from any repo folder
spins up an isolated container you can attach Cursor/VSCode to.
- **Two modes, chosen automatically:**
  - **PR mode** (repo initialized): container gets a short-lived, repo-scoped
  GitHub App token. The agent can push feature branches and open PRs, but
  **cannot merge `main`** (branch protection enforces this).
  - **No-PR mode** (repo not initialized, or `--no-pr`): container gets **no**
  credentials. The agent works locally; **you** push from the host.
- **The container never holds your personal credentials.** No SSH keys, no
`~/.gitconfig`, no host credential helper.
- **Portable & self-contained.** Everything devbox needs lives inside
`.devbox/`. The folder can be moved anywhere. All internal paths resolve 
relative to the CLI's own location.
- **The devbox home must live OUTSIDE any target repo (hard invariant).** Because
`start` bind-mounts the target repo into the container, if `.devbox/` were
inside that repo the container could read the private key and `secrets/ai.env`.
`start` refuses to run if the devbox home is inside (or equal to) the target
repo path, or vice-versa. `.devbox/` is a standalone environment living at its
own top-level location, so it is never nested inside a repo it operates on.
- **Forward-compatible with remote control** (run the container on one machine,
attach from another) without building it now.

---

## 2. Terminology


| Term                     | Meaning                                                                                                                                                      |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **App identity**         | The single GitHub App (`APP_ID` + private key). Installed once on your account; mints short-lived tokens.                                                    |
| **Installation**         | The account-level install of the App. Identified by `INSTALLATION_ID` (same across all your personal repos). Has a repo-access setting: `all` or `selected`. |
| **Project config**       | Per-repo record under `config/<owner>/<repo>/config.env`. Non-secret IDs only.                                                                               |
| **PR mode / No-PR mode** | Whether the container receives a scoped token.                                                                                                               |
| **Base image**           | Shared Docker image `devbox-base` with git, gh, opencode, tooling.                                                                                           |
| **Container**            | Per-repo, persistent container named `devbox-<owner>-<repo>-<shortid>`, where `<shortid>` disambiguates repos that sanitize to the same name.                |


---

## 3. Directory layout (final)

```
.devbox/
├─ .docs/
│  ├─ IMPLEMENTATION-PLAN.md          # this document (local build plan)
│  └─ REMOTE-PLAN.md                  # remote/phone access design (Phase R)
├─ .gitignore                         # ignore secrets, keys, runtime state
├─ requirements.txt                   # PyJWT[crypto], requests
├─ devbox/                            # Python package: CLI + core library
│  ├─ __main__.py                     # `python -m devbox` / dispatcher entry
│  ├─ cli.py                          # arg parsing, subcommand routing
│  ├─ commands/
│  │  ├─ init.py
│  │  ├─ check.py
│  │  └─ start.py
│  └─ core/
│     ├─ paths.py                     # resolve .devbox root relative to __file__
│     ├─ gitctx.py                    # repo root, owner/repo from origin remote
│     ├─ gh.py                        # wrappers around the gh CLI
│     ├─ github_app.py                # JWT, installation token, install detection
│     ├─ config.py                    # read/write project config.env
│     ├─ docker.py                    # build/run/start/exec/attach helpers
│     └─ refresher.py                 # detached token-refresh process
├─ app/                               # THE GitHub App identity
│  ├─ app.env                         # APP_ID, APP_SLUG, bot git identity (non-secret, committed)
│  └─ <name>.private-key.pem          # gitignored
├─ secrets/
│  ├─ ai.env                          # opencode provider key(s) (gitignored)
│  └─ gh.env                          # optional classic PAT for add-repo step (gitignored)
├─ config/
│  └─ <owner>/<repo>/config.env       # per-repo project config (committed, non-secret)
├─ runtime/
│  ├─ Dockerfile
│  ├─ entrypoint.sh                   # LF line endings; chmod +x in image
│  ├─ git-credential-devbox.sh        # reads token from mounted runtime dir
│  └─ gh-wrapper.sh                    # exports GH_TOKEN from token file per call
└─ .run/                              # gitignored runtime state
   └─ <owner>-<repo>/
      ├─ token                        # current installation token (refreshed)
      ├─ fingerprint                  # local run-fingerprint (never in a label)
      └─ refresher.pid
```

The user aliases the CLI, e.g. `devbox="python /path/to/.devbox/devbox"` (or
`python -m devbox`). The alias is the only place the absolute path appears; the
code itself never hardcodes it.

---

## 4. Configuration schemas

### `app/app.env` (committed — non-secret)

```env
APP_ID=4224972
CLIENT_ID=Iv23livRsa4zHAGQgbQG        # optional; may be used as JWT issuer instead of APP_ID
APP_SLUG=                             # auto-populated from GET /app; used for the install URL
GIT_USER_NAME=<app-slug>[bot]
GIT_USER_EMAIL=<APP_ID>+<app-slug>[bot]@users.noreply.github.com
```

`APP_ID` and `CLIENT_ID` are **public, non-secret identifiers** by GitHub's
design (they appear in JWTs, OAuth flows, and the public install URL), so
committing the real values is intentional; the only sensitive material is the
private key. The private key `.pem` sits beside it and is gitignored. `APP_SLUG`
(and the bot git identity) are derived automatically from `GET /app` the first
time devbox runs — no need to look them up by hand.

### `config/<owner>/<repo>/config.env` (committed — non-secret)

```env
OWNER=philteigne
REPO=my-repo
REPO_ID=123456789
DEFAULT_BRANCH=main
INSTALLATION_ID=144647327
BRANCH_PROTECTION=enforced            # enforced | unavailable
APP_REPO_ACCESS=granted              # granted | missing
```

**PR mode is eligible only when `BRANCH_PROTECTION=enforced` AND
`APP_REPO_ACCESS=granted`.** If the default branch could not be protected (e.g. a
private repo on a plan that doesn't support it), the value is `unavailable`; if
the App installation does not actually include this repo, `APP_REPO_ACCESS` is
`missing`. In either case devbox will **refuse to grant a token** — `start` falls
back to No-PR mode as a safety measure. (These are the config-level defaults;
`start` re-verifies both live before minting a token — see §5.3.)

### `secrets/ai.env` (gitignored)

```env
# Set whichever provider you use; all present keys are injected into the container.
OPENCODE_API_KEY=                     # opencode zen gateway (alias: OPENCODE_ZEN_API_KEY)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
OPENROUTER_API_KEY=
OPENCODE_MODEL=                       # optional default model; zen uses opencode/... e.g. opencode/claude-sonnet-4-5
```

Provider is intentionally **deferred/configurable**: devbox injects whatever keys
are present and lets opencode pick. Note: opencode's own hosted gateway ("zen")
is keyed by `OPENCODE_API_KEY` (alias `OPENCODE_ZEN_API_KEY`), **not** a
`ZEN_API_KEY` variable.

---

## 5. Command specifications

All three commands accept an **optional path** (default: current directory) and
resolve it to the enclosing repo root via `git rev-parse --show-toplevel`.
Owner/repo are parsed from the `origin` remote, supporting all common forms:
`https://github.com/<owner>/<repo>(.git)`, `git@github.com:<owner>/<repo>.git`,
and `ssh://git@github.com/<owner>/<repo>.git`.

### 5.1 `devbox init [path]`

Purpose: register a repo you own for PR mode.

1. **Preconditions**
  - Path is inside a git work tree; resolve repo root. Else → error.
  - `origin` remote exists and parses to `owner/repo`. Else → error.
  - `gh` is installed and authenticated (`gh auth status`). Else → error:
  *"Not logged in to GitHub. Run `gh auth login` first."* (hard break)
  - App identity present in `app/` (`app.env` + one `.pem`). Else → error with
  setup instructions.
2. **Ownership check (personal-only for now)**
  - `login = gh api user --jq .login`.
  - Require `owner == login`. If not → error:
  *"devbox currently supports repos owned by your personal account. `<owner>`
  is not `<login>`. (Org support is planned.)"* (hard break)
3. **App install detection** (using an App JWT signed with the `.pem`)
  - `GET /app` → capture `slug` and derive the bot git identity; persist to
   `app/app.env` if not already set.
  - `GET /app/installations` → find the installation whose `account.login == login`.
  - If none → **break**: *"The devbox GitHub App is not installed on your
  account. Install it: [https://github.com/apps/](https://github.com/apps/)**[/installations/new
  then re-run ](https://github.com/apps/)`devbox init`."* (installation cannot be automated)
  - Record `INSTALLATION_ID` and `repository_selection` (`all` | `selected`).
4. **Ensure repo is in scope (must be verified, never assumed)**
  - If `selected` and repo not yet granted:
    - `repo_id = gh api repos/<owner>/<repo> --jq .id`
    - Attempt `PUT /user/installations/<INSTALLATION_ID>/repositories/<repo_id>`.
    - ⚠️ **Auth constraint (verified against GitHub docs):** this endpoint *only*
    works with a **classic PAT that has the `repo` scope**. It does **not**
    work with `gh`'s OAuth token, installation tokens, or fine-grained PATs. So:
      - devbox looks for an opt-in classic PAT (e.g. `DEVBOX_PAT` env or
      `secrets/gh.env`); if present, use it for this one call.
      - If absent or the call is rejected → print the installation settings URL
      (`https://github.com/settings/installations/<INSTALLATION_ID>`) and
      instruct the user to add the repo (one click).
  - **Re-verify access before proceeding.** After any auto-add attempt, confirm
  the repo is actually in scope by minting a **repo-scoped installation token**
  for this single repo (the same call `start` will make) and checking it
  succeeds. Do **not** infer access from an HTTP 2xx on the add-repo call alone.
  - **If access still cannot be confirmed → hard stop (manual action required).**
  Print the settings URL and instruct the user to add the repo, then
  **re-run `devbox init`**. devbox does **not** write a PR-mode config in this
  case. (If a partial config already exists it is marked
  `APP_REPO_ACCESS=missing`, which forces `start` into No-PR mode; PR mode is
  never attempted without confirmed access.)
  - If `selected` and repo already granted (verified), or `all` → record
  `APP_REPO_ACCESS=granted` and continue.
5. **Branch protection (auto)**
  - `default_branch = gh api repos/<owner>/<repo> --jq .default_branch`.
  - **Admin/permission preflight.** Reading and (especially) writing branch
  protection requires **admin** on the repo (fine-grained tokens need
  *Administration: write*). Before attempting the `PUT`, confirm the caller has
  admin — e.g. `gh api repos/<owner>/<repo> --jq .permissions.admin` returns
  `true`. If not admin → do **not** attempt the write; record
  `BRANCH_PROTECTION=unavailable` and warn that PR mode is disabled because the
  account lacks admin on the repo.
  - **Read existing protection first** (`GET …/protection`) and merge, so devbox
  never silently clobbers pre-existing settings (the `PUT` replaces the whole
  config). A `404` here means "no protection yet" (start from empty), not a
  failure.
  - **Explicit error handling on the `PUT`:**
    - `403` → insufficient permission/scope (token lacks Administration write, or
    SSO not authorized) → record `BRANCH_PROTECTION=unavailable`, print an
    actionable message about required admin/scope.
    - `404` → repo not visible to the caller (also possible if the App/token
    can't see it) → treat as unavailable with a clear message.
    - `422` → the protection payload was rejected (e.g. plan doesn't support it,
    or invalid combination) → record `BRANCH_PROTECTION=unavailable`.
    - Any of the above disables PR mode rather than producing a confusing
    half-configured state.
  - Apply protection that **requires ≥1 approving review** before merging
  (`required_pull_request_reviews.required_approving_review_count >= 1`), not
  merely "a PR is required." This is the part that actually blocks the agent
  from self-merging: GitHub forbids a PR author from approving their own PR, so
  the app (author of its own PR) cannot satisfy the review requirement itself.
  A PR-required rule with 0 approvals would let the app merge its own PR.
  - **Safety gate:** branch protection is what prevents the scoped token from
  merging `main`. If it **cannot** be enforced (e.g. on a private repo whose
  plan doesn't support protection), devbox records
  `BRANCH_PROTECTION=unavailable` and **PR mode will be disabled** for this
  repo — `start` will run No-PR mode instead of ever handing out a token.
  `init` warns clearly that PR mode is unavailable and why. (A rulesets-based
  fallback that could re-enable protection is a later option.)
6. **Write config** to `config/<owner>/<repo>/config.env` (create dirs). Log the
  result and whether PR mode is enabled (i.e. whether protection was enforced).

### 5.2 `devbox check [path]` (read-only)

1. Resolve repo root + owner/repo. If not a git repo / no `origin` → log clearly
  and exit.
2. Look for `config/<owner>/<repo>/config.env` and validate required keys.
3. Output (note: `check` reports the cached config; `start` still re-verifies live
  before minting a token):
  - Valid **and** `BRANCH_PROTECTION=enforced` **and** `APP_REPO_ACCESS=granted`
  → *"devbox initialized for `<owner>/<repo>` — will run in **PR mode** (agent
  can open pull requests)."*
  - Valid **but** `BRANCH_PROTECTION=unavailable` → *"devbox initialized, but the
  default branch is not protected — **PR mode is disabled** for safety; will run
  in NO-PR mode."*
  - Valid **but** `APP_REPO_ACCESS=missing` → *"devbox initialized, but the App
  installation doesn't include this repo — **PR mode is disabled**; add the repo
  and re-run `devbox init`. Will run in NO-PR mode."*
  - Missing/invalid → *"devbox not initialized for `<owner>/<repo>` — will run in
  **NO-PR mode** (local only; you push manually)."*

### 5.3 `devbox start [path] [--no-pr]`

1. Resolve repo root + owner/repo.
  - **Containment guard:** refuse to start if the devbox home (`.devbox/`) is
   inside, equal to, or a parent of the target repo path. This prevents the
   bind-mount from ever exposing the private key / secrets to the container.
2. **Docker check** (`docker info`). If not running → error.
3. **Determine mode:** PR mode is *tentatively* chosen only if a valid config
  exists **and** `BRANCH_PROTECTION=enforced` **and** `APP_REPO_ACCESS=granted`
   **and** `--no-pr` not set; otherwise No-PR mode. If a config exists but is not
   eligible, log a clear safety warning (why PR mode is disabled) and proceed in
   No-PR mode (never hand out a token). Log the chosen mode.
  - **Live safety re-check before minting a token (do NOT trust stale config).**
  The entire security model depends on the default branch being protected
  *right now*, but protection could have been weakened or removed since `init`.
  If PR mode was tentatively chosen, `start` re-verifies against the live API
  before minting any token:
    - `GET repos/<owner>/<repo>/branches/<DEFAULT_BRANCH>/protection` and confirm
    `required_pull_request_reviews.required_approving_review_count >= 1`
    (a PR-required rule with 0 approvals is **not** sufficient — the App could
    merge its own PR).
    - Confirm the App can actually mint a repo-scoped token for this repo
    (repo still in the installation's scope).
    - If either check fails → **downgrade to No-PR mode** with a clear warning and
    do not mint a token. (Optionally refresh the cached `BRANCH_PROTECTION` /
    `APP_REPO_ACCESS` values so `check` reflects reality.)
4. **Ensure base image:** if `devbox-base` missing → `docker build -t devbox-base runtime/`.
5. **Container name:** `devbox-<owner>-<repo>-<shortid>` (owner/repo sanitized:
  `/`, uppercase, and illegal chars normalized). Because sanitization is lossy
   (e.g. `my.repo` and `my-repo` both collapse to `my-repo`), append a short
   disambiguator — the `REPO_ID` (or a short hash of `<owner>/<repo>`/`REPO_ID`) —
   so distinct repos can never reuse or clobber each other's container. Also apply
   it as a label for lookups.
6. **Lifecycle (reuse-if-present, with staleness check):**
  - Compute a **fingerprint** of the run-defining inputs (image id, mode, mounts,
   default branch, and hashes of `entrypoint.sh` / credential helper). For the
   secret-bearing inputs (AI keys / `secrets/ai.env`), hash the **file
   content**, never the raw values — and never place any secret-derived material
   in an inspectable Docker label.
  - **Storage:** keep the full fingerprint in a **local state file**
  (`.run/<owner>-<repo>/fingerprint`, gitignored), not a container label.
  Optionally store only a short **non-secret** digest as a label purely for
  lookup. Comparison is done against the local file.
  - Running & fingerprint matches → reuse (print attach instructions).
  - Exists (stopped) & fingerprint matches → `docker start`, then reuse.
  - Exists but fingerprint **differs** → remove and recreate (config/runtime
  changed; a stale container would silently keep old env/mounts).
  - Absent → `docker run -d` (persistent; **not** `--rm`), keeping it alive
  (`sleep infinity`) so it can be attached now or later.
7. **Mounts & env:**
  - Bind-mount the **repo root** → `/workspace`.
  - Always inject: `GIT_USER_NAME`, `GIT_USER_EMAIL` (bot identity in PR mode; a
  neutral local identity in No-PR mode), `DEFAULT_BRANCH`, `OWNER`, `REPO`,
  `MODE`, and any provider keys / `OPENCODE_MODEL` from `secrets/ai.env`.
  - **PR mode only:** mount `.run/<owner>-<repo>/` → `/devbox-run`. The token is
  delivered via a **file** (not a static env var) so the refresher can update it.
  - **No-PR mode:** no token mount; no host credential paths mounted.
8. **Entrypoint (inside container):**
  - Set `git config user.name/email` and
   `git config --global --add safe.directory /workspace` (avoids Git "dubious
   ownership" on the bind-mounted repo).
  - **PR mode (credential-helper ONLY — no token in any URL or git config):**
    - Configure the git credential helper (`git-credential-devbox.sh`) which
    reads the **current** token from `/devbox-run/token` at call time (so
    refresh is transparent). The token is never written into git config or a
    remote URL. The helper parses git's stdin request and only responds when
    `protocol=https` and `host=github.com`, emitting **both**
    `username=x-access-token` and `password=<token>` (GitHub HTTPS with an
    installation token expects the `x-access-token` username; password-only can
    fail depending on Git's credential/prompt flow).
    - Rewrite SSH remotes to HTTPS with a **token-less** insteadOf so the helper
    can supply the credentials, covering **both** SSH URL forms:
      - `url."https://github.com/".insteadOf "git@github.com:"`
      - `url."https://github.com/".insteadOf "ssh://git@github.com/"`
      Then ensure `origin` itself is the HTTPS form — simplest is to normalize
      `origin` directly to `https://github.com/<owner>/<repo>.git`.
    - For `gh` operations (e.g. `gh pr create`): a small `**gh` wrapper** on
    `PATH` exports `GH_TOKEN=$(cat /devbox-run/token)` per invocation, since
    `gh` does not read git credential helpers.
  - **No-PR mode:** unset any credential helper and set a failing `GIT_ASKPASS`
  so accidental pushes fail fast instead of prompting.
  - opencode is baked into the image; provider key(s) via env means no
  interactive login is needed.
  - Keep the container alive for attach.
9. **PR mode token lifecycle:**
  - Mint an initial **repo-scoped** installation token (token-creation body
   `{"repositories": ["<repo>"]}`) → write to `.run/<owner>-<repo>/token`.
  - **Atomic writes + tight perms:** the initial write and every refresh write to
  a temp file then `os.replace()` (atomic rename), with `chmod 600`, so a
  concurrent git/gh read never sees a partial or truncated token.
  - Launch a **detached host refresher** (`core/refresher.py`) that re-mints
  every ~50 min, rewrites the token file atomically, and exits when the
  container stops (writes its pid to `.run/<owner>-<repo>/refresher.pid`).
10. **Attach instructions:** print `docker exec -it <container-name> bash` (the
  full `devbox-<owner>-<repo>-<shortid>` name) and the Cursor/VSCode "Attach to
    Running Container" hint, plus the active mode and PR capability.

---

## 6. Runtime (container) design

- **Base image (`runtime/Dockerfile`):** `python:3.12-slim` (or similar) + `bash`,
`git`, `curl`, `ca-certificates`, the **GitHub CLI** (`gh`), and **opencode**
(installed via its official install script at build time). `entrypoint.sh`,
`git-credential-devbox.sh`, and `gh-wrapper.sh` copied in and `chmod +x`.
- **opencode** runs the AI work; it is treated as swappable ("for now").
- **Credential helper (`git-credential-devbox.sh`):** on a `get` request, parses
git's stdin and responds **only** when `protocol=https` and `host=github.com`,
emitting `username=x-access-token` and
`password=<contents of /devbox-run/token>`; nothing otherwise. Emitting the
`x-access-token` username (not just a password) is what GitHub HTTPS expects for
installation tokens. The token is only ever read from the file at call time —
never stored in git config or a remote URL — so refresh is transparent.
- `**gh` wrapper (`gh-wrapper.sh`):** shadows `gh` on `PATH`; sets
`GH_TOKEN=$(cat /devbox-run/token)` then execs real `gh`, because `gh` ignores
git credential helpers. (PR mode only.)
- **Repo ownership:** bind mounts preserve host UID/GID. To avoid write failures
and Git "dubious ownership" on Linux, run the container with
`--user <host-uid>:<host-gid>` (Linux) and always set
`safe.directory /workspace`. On Docker Desktop (mac/win) the file-sharing layer
handles perms; `safe.directory` is still set.
- **Line endings:** shell scripts committed with LF (`.gitattributes`) so they run
correctly in the Linux container regardless of host OS.

---

## 7. Security model (summary)

- **No merges to `main`:** enforced by branch protection that **requires ≥1
approving review** + a token with no admin. Direct pushes to `main` are
rejected, and the agent cannot self-merge its PR because GitHub forbids a PR
author from approving their own PR. (A PR-required rule with 0 approvals would
NOT be sufficient — the app could merge its own PR.)
- **PR mode requires enforced branch protection, verified live.** If protection
can't be set, devbox refuses to grant any token and runs No-PR mode — a token is
never issued for an unprotected default branch. `start` does **not** trust the
cached config: it re-checks the live default-branch protection (≥1 required
approving review) and confirms the repo is still in the App's scope immediately
before minting a token, downgrading to No-PR mode if either has changed since
`init`.
- **Repo-scoped tokens:** each session's token is limited to the single target
repo via the token-creation `repositories` parameter — independent of what the
installation can otherwise access.
- **Scope of what the token CAN do (by design):** push arbitrary **feature
branches** and open PRs — this is intended, it's how the agent proposes work.
Branch protection covers only the default branch. Tags are *not* covered by
branch protection; if protecting release tags matters, add a tag ruleset
(optional hardening, not required for the feature→PR workflow).
- **Devbox home outside the mount:** enforced by the containment guard so the
container can never read the `.pem` / `secrets/`.
- **No personal credentials in the container:** private key stays on the host;
only a short-lived token file is mounted (PR mode) or nothing (No-PR mode). No
`~/.ssh`, `~/.gitconfig`, or host credential helper is ever mounted. The token
is delivered by file + credential helper only — never embedded in git config.
- **Filesystem isolation:** only the target repo is mounted; no other host paths.
- **Least privilege GitHub App:** Contents R/W, Pull requests R/W, Metadata read.
No Administration/Workflows/Secrets.
- **Residual risk:** the container has general internet access (for the model API
and package installs); it is not air-gapped. True network lockdown is a separate
future option.

---

## 8. Prerequisites (host)

- **Docker** (Desktop on macOS/Windows, engine on Linux), running.
- **GitHub CLI (`gh`)**, installed and authenticated (`gh auth login`).
- *(Optional)* a **classic PAT with `repo` scope** (in `secrets/gh.env`) if you
want `init` to auto-add a repo to a "selected repositories" installation;
otherwise `init` guides you to add it via the installation settings page.
- **git**.
- **Python 3.10+** with deps from `requirements.txt` (`PyJWT[crypto]`, `requests`).
- The devbox **GitHub App installed once** on your account (init detects & guides).

---

## 9. Cross-platform notes (mac / linux / windows)

- All host logic is **Python** → runs on all three.
- `entrypoint.sh` and the credential helper run **inside the Linux container**, so
no host shell (bash/pwsh) dependency; Windows is fine.
- Docker Desktop handles Windows path mounting; the CLI passes the resolved repo
root and lets Docker translate.
- Shell scripts forced to **LF** via `.gitattributes` to avoid CRLF breakage.

---

## 10. Forward-compat: remote control (NOT built now)

Captured only to avoid architectural dead-ends:

- **Configurable Docker host/context** (env/config), so containers can run on a
remote machine (`docker -H ssh://…` / contexts) without code changes.
- **Persistent named containers** (already chosen) so they can be attached to
later and from other machines (Cursor/VSCode remote attach over SSH).
- **CLI as a thin layer over `core/`** so a future small HTTP server can reuse the
same functions for laptop/phone control.
- **opencode `serve`** mode could later be the remote interaction surface.

None of the above is implemented in the initial version.

---

## 11. Open questions / deferred

- **AI provider** left configurable; a concrete default model can be set later in
`secrets/ai.env`.
- **Org-owned repos** and org installations (init currently personal-only).
- **Private-repo branch protection** on free plans: currently **disables PR mode**
(safety). A rulesets-based fallback that could re-enable protection is a possible
later enhancement.
- **Headless/background task mode** (agent opens a PR unattended) — deferred; the
current design is attach-and-work.
- **Multiple app identities / multiple agents** — single app identity for now.
- `**APP_SLUG`** is auto-fetched from `GET /app` — no manual entry needed.

---

## 12. Implementation phases

- **Phase 0 — Scaffold:** create the `.devbox/` structure, place the App
identity (`.pem` + `app.env`) under `app/`, add `.gitignore`,
`requirements.txt`, and the `devbox` package skeleton.
- **Phase 1 — Core library:** `paths`, `gitctx`, `gh`, `github_app`, `config`,
`docker` helpers.
- **Phase 2 — `devbox check`:** read-only; easiest to validate end-to-end.
- **Phase 3 — `devbox init`:** install detection, add-repo-to-selection, branch
protection, write config.
- **Phase 4 — Runtime + `devbox start` (No-PR mode):** Dockerfile, entrypoint,
base image build, mount + launch + attach UX.
- **Phase 5 — PR mode:** repo-scoped token, credential helper wiring, detached
refresher, push/PR verified.
- **Phase 6 — Polish:** error messages, logging, attach instructions, docs update.


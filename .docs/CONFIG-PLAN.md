# Configurable Devbox Environment Plan

## Summary

Devbox should stop installing optional developer and AI tooling in every image.
The default environment should contain only the software required for devbox to
provide its core Git, GitHub, and shell behavior. OpenCode, Node/npm, nvm, fvm,
Flutter, and other project-specific tools should be opt-in.

The first implementation will read one trusted, versioned TOML file from the
devbox installation itself:

```text
<devbox-home>/devbox.toml
```

In this repository that path is `.devbox/devbox.toml`. A missing file and an
empty file with the current schema version will both select the clean default.
Devbox will not read configuration from the target repository in this phase.
This is installation-owner configuration: a normal installation is owned by
one OS user, but two OS users sharing one devbox checkout also share this file.
An OS-user-specific overlay for a shared installation is outside phase one.

Package installation belongs at image-build time, not shell-launch time. Shell
launch configuration should be limited to fast environment setup, such as
sourcing nvm/RVM or adding a tool to `PATH`. Mutable caches and user state belong
under a stable, writable container home. This avoids downloading and installing
the same tools each time a user runs `docker exec ... bash`, while still letting
version managers operate normally inside a persistent container.

## Definition of the clean default

The proposed default image has two categories:

1. **Devbox runtime dependencies** remain installed because the container needs
   them to preserve its existing behavior: Bash, CA certificates, Git, and
   GitHub CLI. The host-side applet's use of Python does not require Python in
   the container. Use a pinned Debian slim base rather than a Python base unless
   a separate container-side Python requirement is established. `curl` and
   `gnupg` may be retained only where the image build needs them, and should be
   removed from the final layer when practical.
2. **User/project tooling** is absent unless configured. This includes
   OpenCode, Node/npm, nvm, fvm, Flutter, language SDKs, compilers, and similar
   packages.

This plan uses "clean" to mean no optional user/project tooling. Removing Git
or GitHub CLI as well would change the current PR-mode contract and should be a
separate product decision.

## Current behavior and constraints

- `runtime/Dockerfile` currently installs OpenCode unconditionally with
  `curl -fsSL https://opencode.ai/install | bash`.
- The same Dockerfile installs Bash, certificates, curl, Git, gnupg, and `gh`.
- `devbox/core/docker.py` manages one shared `devbox-base` image. Its runtime
  label changes only when files below `runtime/` change.
- `devbox start` creates a persistent container per repository and reuses it
  while its fingerprint still matches.
- `runtime/entrypoint.sh` runs when a container is created, not on every later
  `docker exec` shell. Therefore it is not a reliable shell-initialization
  mechanism.
- `secrets/ai.env` is injected into the container independently of whether an
  AI client is installed. That behavior can remain; an API key does not imply
  that OpenCode must be present.
- The standalone devbox home and a target repository are intentionally not
  allowed to overlap. The central config described here stays on the trusted
  side of that boundary.

## Goals

- Make the no-config experience a minimal, usable devbox with no OpenCode or
  project toolchains.
- Let the owner opt into system packages and arbitrary tool installers without
  editing `runtime/Dockerfile`.
- Rebuild the image and recreate stale repository containers whenever the
  effective tooling config changes.
- Separate one-time image provisioning from per-shell initialization.
- Validate configuration early and fail with a useful path and field name.
- Keep raw secrets and offline-guessable secret hashes out of the config,
  Docker build context, image history, labels, and fingerprints. Use only a
  locally keyed, non-reversible revision digest where container invalidation
  requires detecting a runtime-secret change.
- Give shell-based version managers a stable user, writable home, predictable
  install root, and consistent interactive/non-interactive Bash behavior.
- Preserve PR mode, NO-PR mode, token refresh, and container isolation.
- Establish a schema that can later accept a target-repository override.

## Non-goals for the first phase

- Reading `<target-repo>/.devbox/devbox.toml`.
- Supporting different tooling configs for multiple OS users who share one
  devbox checkout.
- Supporting shells other than Bash.
- Creating a full package manager or resolving dependencies between tools.
- Automatically translating host-installed tools into container tools.
- Installing or updating packages every time an interactive shell opens.
- Passing secrets into image builds.
- Maintaining first-class installers for every language or CLI.

## Proposed configuration

Use TOML because Python 3.12 includes `tomllib`, so this adds no runtime
dependency. Commit a clean default `devbox.toml` with the applet; treat it as
non-secret, portable configuration.

### Empty default

```toml
schema_version = 1

[build]
apt_packages = []
root_commands = []
user_commands = []

[environment]

[shell]
path_prepend = []
init = []
```

The file is optional. If it is missing, devbox should behave exactly as if the
empty default above were present.

### Customized example

```toml
schema_version = 1

[build]
apt_packages = [
  "build-essential",
  "bzip2",
  "curl",
  "gnupg",
  "tar",
  "unzip",
  "xz-utils",
  "zip",
]

# System-level setup only. These run as root during the image build.
root_commands = [
  "system-setup-command-goes-here",
]

# Version managers and user tooling belong here. These run as the final devbox
# user with DEVBOX_TOOL_ROOT=/opt/devbox. Configured versions should be installed
# here so they can be reconstructed from pinned inputs and shared through the
# Docker image.
user_commands = [
  "install-opencode-under-$DEVBOX_TOOL_ROOT-command-goes-here",
  "install-nvm-and-the-selected-node-version-command-goes-here",
  "install-fvm-command-goes-here",
  "install-rvm-and-the-selected-ruby-version-command-goes-here",
]

[environment]
# Available to user build commands and at container runtime. HOME and PATH are
# reserved and managed separately by devbox.
NVM_DIR = "/opt/devbox/nvm"
FVM_INSTALL_DIR = "/opt/devbox/fvm"
FVM_CACHE_PATH = "/home/devbox/.cache/fvm"
rvm_path = "/opt/devbox/rvm"

[shell]
# Devbox prepends these entries to the existing image PATH without requiring a
# literal "$PATH" value in TOML.
path_prepend = [
  "/opt/devbox/bin",
  "/opt/devbox/fvm/bin",
]

# These run for each interactive Bash shell. They should only initialize tools;
# they should not download packages or modify the image.
init = [
  '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"',
  '[ -s "$rvm_path/scripts/rvm" ] && . "$rvm_path/scripts/rvm"',
]
```

The implementation documentation should replace the placeholder commands with
tested, pinned examples for OpenCode, nvm/Node/npm, FVM, and RVM/Ruby. They
should be examples rather than defaults.

### Schema rules

- `schema_version` is required when the file exists and initially must be `1`.
- Unknown sections and keys are errors. Silent typos would otherwise produce a
  container missing expected tools.
- `build.apt_packages` is a list of Debian package names. Validate names with a
  conservative package-name pattern and deduplicate them while retaining their
  declared order.
- `build.root_commands` is an ordered list of non-empty Bash command strings
  executed as root inside the Docker build. It is intended for system setup
  that cannot be expressed as an apt package.
- `build.user_commands` is an ordered list of non-empty Bash command strings
  executed as the final unprivileged devbox user. This is the preferred place
  for nvm, FVM, RVM, OpenCode, language versions, and user-level packages.
- `environment` is a string-to-string table made available to user build
  commands, the container process environment, and shell initialization.
- `shell.path_prepend` is an ordered list of absolute paths. Devbox constructs a
  final `PATH` from these entries plus the image's existing `PATH`; users do not
  put an unexpanded `$PATH` string in TOML.
- `shell.init` is an ordered list of non-empty Bash command strings.
- Empty tables/lists are valid.
- Secret-bearing build settings are deliberately unsupported. Documentation
  must direct API keys and tokens to the existing gitignored secret mechanism.
  After reading `secrets/ai.env`, reject any key that is also present in
  `environment`; the runtime secret overlay must never silently replace a
  tooling value or vice versa. Apply the same reserved-variable rules to
  `ai.env` itself so it cannot define `MODE`, Git/GitHub credentials, config
  paths, or other devbox safety variables; it is a secret source, not an
  unrestricted second configuration file.
- Config errors stop `devbox start` before any image or container mutation.

Arbitrary build commands are intentionally powerful. The central config is
trusted owner-controlled input, and the commands run inside an isolated Docker
build, not directly on the host. Root commands should be used sparingly; user
commands are the normal extension point. Devbox must still ensure that the
Docker build context contains only explicitly staged runtime/config files so a
build command cannot read `app/*.pem`, `secrets/`, project files, or `.run`
state.

Reserve and reject user definitions of `HOME`, `PATH`, `BASH_ENV`, `ENV`,
`SHELLOPTS`, `BASHOPTS`, `MODE`, `OWNER`, `REPO`, `DEFAULT_BRANCH`,
`GIT_USER_NAME`, `GIT_USER_EMAIL`, `GIT_ASKPASS`, `SSH_ASKPASS`,
`GIT_TERMINAL_PROMPT`, `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`,
`GIT_CONFIG_NOSYSTEM`, `GIT_CONFIG_COUNT`, `GIT_DIR`, `GIT_WORK_TREE`,
`GIT_SSH_COMMAND`, `GH_TOKEN`, `GITHUB_TOKEN`, and `GH_CONFIG_DIR`. Also reject
the indexed `GIT_CONFIG_KEY_*` and `GIT_CONFIG_VALUE_*` families and any key
loaded from `secrets/ai.env`. These variables have explicit devbox safety or
secret behavior below and cannot be controlled by tooling config. Keep this
list centralized in code and use it for both validation and diagnostics.

## Runtime identity and tool-storage contract

Shell-based version managers assume a consistent user identity, a writable
home, and startup scripts that are sourced in the current shell. Devbox should
provide that contract rather than expecting every installer command to solve it
independently.

### Stable devbox user

- Build the image with `DEVBOX_UID` and `DEVBOX_GID` arguments derived from the
  host user. Include both values in the build identity. On hosts where numeric
  ownership is not exposed, use the documented Docker Desktop default
  `1000:1000` and include that choice in the identity. Refuse to run devbox as
  host UID 0 rather than silently creating a root-equivalent `devbox` account.
- Validate both IDs as non-zero platform integers. Create a named `devbox` user
  whose numeric UID/GID match those values. If the GID already exists, use that
  group as the primary group without renaming it. If the UID already belongs to
  any account other than `devbox`, fail the build with an actionable collision
  error; do not create duplicate passwd entries. The pinned base image and
  baseline packages must be tested not to claim the documented default ID.
- Make this user the final image user and stop relying on a runtime-only
  `docker run --user` override.
- Create `/opt/devbox`, `/home/devbox`, and `/run/devbox` during the build with
  ownership by the devbox user. User provisioning runs as this same user.
- During `build.user_commands`, set the build-only `HOME=/opt/devbox` and
  `DEVBOX_TOOL_ROOT=/opt/devbox`. This accommodates installers that insist on a
  location below the current home without placing image-baked tools in the
  runtime home that will later be covered by a mount. After provisioning,
  restore the final runtime `HOME=/home/devbox`.
- Set `HOME=/home/devbox` in the image and in the container configuration. Do
  not rely on an `export HOME=...` performed only by the entrypoint process.

This keeps bind-mounted workspace ownership correct on native Linux and gives
installers a real passwd entry and stable home on Docker Desktop and Linux.

### Immutable tools and mutable state

- `/opt/devbox` is the configured tool root. The image build installs version
  managers and selected language versions there. Its intended contents are
  reconstructible from `devbox.toml` and shared through Docker image layers;
  bit-for-bit reproducibility additionally requires pinned base images, package
  repositories/versions, installers, and downloaded artifacts.
- `/home/devbox` holds mutable user state: npm/gem/pub caches, FVM's Flutter SDK
  cache, shell history, and user-level tool configuration.
- Always bind-mount a per-repository host directory such as
  `.run/<collision-resistant-state-key>/home` at `/home/devbox`, in both PR and
  NO-PR modes. The state key must include the repository ID or owner/repo digest;
  a sanitized owner/repo name alone is not unique. Create the directory
  host-side as the current user with mode `0700` before `docker create`. NO-PR
  mode mounts only this home directory, never the token-bearing parent run
  directory.
- Include the home mount path in the container fingerprint.
- Keep devbox-owned Git and GitHub CLI configuration out of the persistent home.
  Set `GIT_CONFIG_GLOBAL=/run/devbox/gitconfig` and
  `GH_CONFIG_DIR=/run/devbox/gh` in the container configuration; the entrypoint
  creates these ephemeral, mode-specific files/directories idempotently.
  Configure Git identity and `safe.directory` there. In PR mode, add the devbox
  credential helper and both SSH-to-HTTPS rewrites. In NO-PR mode, omit all
  helpers and rewrites. Never change the mounted repository's stored `origin`
  URL. This prevents PR-mode settings and a persisted `gh auth` store from
  becoming active after a transition to NO-PR mode.
- Bake `/usr/local/bin/devbox-askpass-fail` into the image. In NO-PR mode set
  `GIT_ASKPASS`, `SSH_ASKPASS`, and `GIT_TERMINAL_PROMPT=0` in Docker's container
  environment, not only with entrypoint `export` statements: later
  `docker exec` processes inherit the former but not environment mutations made
  by PID 1. The `gh` wrapper must use only `/devbox-run/token` in PR mode and the
  ephemeral `GH_CONFIG_DIR`; it must not fall through to credentials persisted
  under `/home/devbox`.

Configured image-baked versions are the source of truth. A user may run
`nvm install`, `rvm install`, or similar commands interactively because
`/opt/devbox` is owned by the devbox user and the container is persistent. Those
ad hoc changes live in the container's copy-on-write layer and may disappear
when a config/runtime change recreates the container; versions that must survive
recreation belong in `build.user_commands`. Mutable caches under
`/home/devbox` do survive container recreation.

### Version-manager behavior

- **nvm:** set `NVM_DIR` below `/opt/devbox`, install nvm and configured Node
  versions as the devbox user, and source `nvm.sh` in the current shell. nvm is a
  shell function and modifies `PATH`; checking only for an executable is not
  sufficient.
- **FVM:** install the standalone FVM executable below `/opt/devbox`, prepend its
  `bin` directory, and set `FVM_CACHE_PATH` below `/home/devbox/.cache` so
  downloaded Flutter SDKs are writable and persistent. Project `.fvmrc` remains
  in `/workspace` as normal. Unlike nvm/RVM runtimes baked under `/opt/devbox`,
  FVM-managed Flutter SDKs are per-repository mutable cache state and are
  populated by an explicit `fvm install/use`, never merely by opening a shell.
- **RVM:** install as the devbox user with `rvm_path` below `/opt/devbox`, install
  configured Ruby versions at image-build time, and source `scripts/rvm` in the
  current shell. RVM must be loaded as a function for `rvm use` to change the
  current environment.

The opt-in examples added during implementation must test these exact layouts
and pin installer/version inputs. Installers must be told not to edit
`~/.bashrc` or other dotfiles; devbox owns the generated shell hook. Each
example must also declare installer/runtime prerequisites such as curl, tar,
compiler packages, or signature-verification tools in `build.apt_packages`,
because the clean baseline does not promise those optional utilities.

## Provisioning and shell model

### Image provisioning

1. Parse and validate `<devbox-home>/devbox.toml`, read only the names from
   `secrets/ai.env` needed for collision validation, and resolve host identity
   before live GitHub checks, token minting, or any Docker mutation.
2. Compute an effective-config digest from normalized schema data. The digest
   must include command order and environment values, but never data from
   `secrets/`.
3. Generate an isolated Docker build context in a temporary directory below
   `.run/build/` that
   contains only:
   - the existing whitelisted runtime files;
   - generated root/user provisioning scripts for apt packages and build
     commands; and
   - a generated shell-init script for `environment`, `shell.path_prepend`, and
     `shell.init`.
4. Build `devbox-base` using that isolated context. Do not change the build
   context to the devbox repository root, because that would send private keys
   and secrets to the Docker builder.
5. Install the operational baseline and configured apt packages first, resolve
   any resulting UID/GID collisions, create the final devbox user/directories,
   run root commands, then switch to that user and run user provisioning with
   `environment` values exported. Use strict shell settings for build scripts
   and clean apt metadata in the same layer.
6. Store both the runtime hash and effective-config digest in non-secret image
   labels. A mismatch causes a rebuild. Docker layer caching should preserve the
   unchanged baseline when only custom tooling changes.
7. Tag images by the complete build-input identity, for example
   `devbox-base:<runtime-and-config-digest>`, and return that cache tag plus its
   inspected immutable image ID. `create_container` must use the exact image ID
   instead of always using the mutable `devbox-base` tag. Because the container
   fingerprint includes the image ID, each affected repository container will
   be recreated on its next `devbox start`.

These tags are input-addressed cache keys, not claims of bit-for-bit image
reproducibility: apt repositories and remote artifacts can change unless every
source is pinned. Pin the Debian base by OCI digest and pin and verify every
documented installer example. Add `devbox start --rebuild-image` to rebuild and
replace an existing tag deliberately when upstream inputs must be refreshed;
the newly produced image ID still participates in container invalidation.

The build should fail on the first unsuccessful apt install or custom command,
show which config command and phase failed, and leave the last working container
alone. Build and smoke-test the replacement image before changing a stale
container.

Stage build contexts atomically: write a uniquely named temporary directory,
finish and validate every file, then rename it to its digest path. Use a
cross-platform exclusive build-identity lock so concurrent starts do not build
the same image twice. Input-addressed image tags prevent two different
configurations from racing over one mutable `devbox-base` tag. Clean temporary
contexts after the build; keep no secrets in them.

### Interactive shell initialization

Generate `/etc/devbox/shell-init.sh` in the image. Source it for interactive Bash
sessions through the system Bash startup path, and set
`BASH_ENV=/etc/devbox/bash-env` so non-interactive Bash commands source the same
initialization. This follows the behavior required by shell-function managers
such as nvm and RVM. Add a shell-local, non-exported guard so it is not sourced
twice in one shell but is still sourced in child Bash processes.

This script should:

- expose validated `environment` values using safe shell quoting;
- prepend `shell.path_prepend` entries to the existing `PATH`, removing
  duplicates without evaluating shell text;
- run `shell.init` entries in declaration order; and
- for an initializer that returns normally with a failure status, produce a
  clear warning naming the failing entry and continue opening the shell.

It should not perform network access or package installation. That separation
is a documented contract, even though arbitrary shell commands cannot be
perfectly policed.

Initialization must run in the current shell so functions and environment
changes from nvm/RVM persist. Snapshot and restore ordinary shell options,
`shopt` settings, traps, working directory, and `IFS` after each entry returns,
and wrap each entry in an explicit success check so an ordinary non-zero status
does not terminate the shell. This is best-effort containment for trusted
initialization, not a sandbox: an arbitrary entry can call `exit`, `exec`, make
state readonly, or otherwise prevent restoration. Document that `shell.init` is
trusted code and that such control-flow commands can terminate or replace the
shell. A subprocess cannot provide stronger containment while preserving nvm or
RVM functions in the caller.

Any environment needed by non-interactive processes should also be included in
the container environment during `docker create`. Shell startup files alone do
not affect commands such as `docker exec <container> <tool>`.

## Code changes

### 1. Add a tooling-config model

Create `devbox/core/tooling_config.py` rather than expanding
`devbox/core/config.py`, which currently owns GitHub/PR project metadata.

The new module should provide:

- immutable dataclasses for the build, environment, and shell sections;
- `load_tooling_config(path: Path) -> ToolingConfig`;
- schema/type/value validation with `DevboxError` messages;
- normalization used for stable hashing; and
- a hash method that covers the entire effective non-secret config.

Add `paths.tooling_config_path()` returning
`paths.devbox_home() / "devbox.toml"`.

### 2. Make the Docker build config-aware

Update `devbox/core/docker.py` so `ensure_base_image` accepts the validated
tooling config, resolved identity, and an explicit force-rebuild flag. Replace
the single runtime-only label comparison with a build identity containing:

- runtime-file hash;
- effective tooling-config hash; and
- schema version;
- resolved devbox UID/GID; and
- any default platform identity used when host numeric IDs are unavailable.

Add small, independently testable functions for:

- constructing the normalized build identity;
- rendering the root and user provisioning scripts;
- rendering the shell-init script with safe quoting; and
- staging the allowlisted build context atomically.

Do not interpolate package names or environment values directly into an
unquoted Docker command. Render them into files using deliberate shell quoting,
then `COPY` those generated files into the build.

### 3. Make the runtime image minimal

Update `runtime/Dockerfile` to:

- replace the Python base with a Debian slim image pinned by OCI digest;
- remove the unconditional OpenCode installer;
- retain only agreed devbox runtime dependencies;
- create the host-UID/GID-matched devbox user and owned tool/home directories;
- copy and execute separate root and user provisioning scripts;
- install the generated shell-init script; and
- ensure the shell-init hook works for the actual `docker exec -it ... bash`
  attach command on both Docker Desktop and native Linux.

The implementation must verify that interactive shells, non-interactive Bash,
direct `docker exec` commands, and editor-attached processes receive the
documented UID/GID, `HOME`, environment, and static `PATH`. Commands that depend
on a shell function are invoked through Bash; direct `docker exec <name> node`
is only guaranteed when the selected executable's directory is part of the
static configured `PATH`.

### 4. Wire config into `devbox start`

In `devbox/commands/start.py`:

1. load/validate the central tooling config before live GitHub or Docker work;
2. pass it and the resolved UID/GID to `ensure_base_image`;
3. create/mount the persistent per-repository home and pass `environment`,
   `HOME`, `BASH_ENV`, the ephemeral Git/GitHub configuration paths, mode safety
   variables, and the constructed static `PATH` into the container; and
4. compute the container fingerprint from a canonical representation of the
   complete non-secret container specification: tooling digest, exact image ID,
   user, command, workdir, mode, all non-secret environment, every bind source
   and destination, and relevant file identities. Do not maintain a selective
   hand-written list that can silently omit a newly added container setting.

Add `--rebuild-image` to `devbox start` and pass it through to
`ensure_base_image`. It bypasses an existing input-addressed cache tag, rebuilds
under the normal build-identity lock, and uses the newly inspected image ID.

Update `ContainerSpec.state_key` and `run_state_dir` together so the same
repository-ID/digest disambiguator used in container names also scopes tokens,
fingerprints, locks, and persistent homes. Do not recompute a lossy state path
from only sanitized owner/repo text.

Keep secrets from `secrets/ai.env` as the final runtime-only overlay. Reject a
collision between `environment` and reserved devbox variables (`MODE`, `OWNER`,
`REPO`, `DEFAULT_BRANCH`, Git identity variables, and credential variables) so
tooling config cannot accidentally alter safety behavior.

Secret changes must also invalidate a container without storing a raw or
offline-guessable hash. On first use, create a random 256-bit HMAC key at
`secrets/.fingerprint-key` with mode `0600`; it is gitignored and never mounted
or sent to Docker. Add an HMAC-SHA-256 revision of the canonical `ai.env`
contents to the host-side container fingerprint, but never to image labels or
container labels. This allows reliable secret rotation without exposing a
dictionary-testable digest. Atomically replace the revision when writing the
new fingerprint.

### 5. Make replacement atomic

Add a per-repository lifecycle lock, an atomically written non-secret lifecycle
journal, and startup reconciliation. Before mutating an existing canonical or
backup container, require `devbox.managed=true` and the expected repository
identity label. If the canonical name is occupied by an unmanaged or differently
owned container, stop with an actionable error; never remove or rename it.

Replace stale containers with this rollback- and interruption-safe flow:

1. build the input-addressed candidate image;
2. run a credential-free smoke container with a temporary home and verify its
   entrypoint plus `bash -lc` initialization;
3. write a journal containing the canonical name, unique backup name, old
   non-secret container identity, candidate image ID, and transaction phase;
4. stop and rename the existing repository container to the journaled backup
   name, updating the journal after each completed operation;
5. create/start the candidate under the canonical container name with managed,
   repository-identity, build-identity, and non-secret-spec-digest labels;
6. verify it remains running for a bounded stabilization period and a Bash smoke
   command succeeds;
7. on failure, remove only the verified managed candidate, rename/start the
   verified backup, keep the previous fingerprint, and clear the journal only
   after rollback succeeds; and
8. on success, atomically write the new fingerprint, remove the verified backup,
   and clear the journal.

At the beginning of every locked `devbox start`, reconcile an existing journal
and uniquely named backups before normal inspection. The journal plus managed
identity labels must make every interruption point recoverable without guessing
which container belongs to devbox. The secret HMAC revision remains only in the
host fingerprint; container labels use the non-secret specification digest.

Coordinate the token refresher with this lock: start/restart it only after the
new canonical container passes verification, and ensure rollback restores a
working refresher for PR mode. Never pass PR tokens or AI secrets to the generic
image smoke container.

Perform the live PR safety check before provisioning, but mint or re-mint the
short-lived installation token only after image build and generic smoke checks,
immediately before candidate creation. Split the current combined safety/token
operation accordingly. Under the lifecycle lock, stop and verify termination of
the old refresher before atomically writing the newly minted token, so it cannot
race and overwrite that token. This prevents a long Node/Flutter/Ruby build from
consuming the token lifetime before the container starts. Validate more than a
reused PID before deciding that a recorded refresher still belongs to this
repository, and start the replacement only after candidate verification.

### 6. Add config diagnostics

Add a read-only CLI command:

```text
devbox config check
```

It should print:

- the resolved config path;
- whether the clean implicit default or a file was loaded;
- the schema version;
- configured apt-package/root-command/user-command/path/init counts; and
- the effective digest.

It must not print full commands, environment values, AI keys, or other secrets.
`devbox start` should provide the same actionable validation errors without
requiring this command first.

### 7. Add the default and examples

- Add a committed `devbox.toml` containing the empty schema.
- Add documentation with opt-in, pinned examples for OpenCode, Node/npm through
  nvm, Flutter through FVM, and Ruby through RVM.
- Explain that npm is supplied by the selected Node installation and normally
  should not be installed as a separate global package.
- Explain that a config change affects a repository the next time
  `devbox start` runs for it.
- Keep actual API keys in `secrets/ai.env`; do not put them in `devbox.toml`.

## Test plan

### Unit tests

- Missing config produces the empty clean default.
- The committed empty config parses successfully.
- Unsupported schema versions, unknown keys, wrong types, invalid apt package
  names, invalid environment names, and empty commands fail clearly.
- Normalization is deterministic; mapping order does not change the digest,
  while apt/command/init order does.
- Generated shell exports safely quote whitespace, quotes, dollar signs, and
  other shell metacharacters.
- Reserved environment-variable collisions are rejected.
- Dynamic collisions with keys from `secrets/ai.env` are rejected without
  including secret values in diagnostics.
- Host UID/GID and config changes alter the build identity; UID 0 and conflicting
  existing UIDs fail clearly, while an existing GID is reused safely.
- Static `PATH` prepending preserves the base path, order, and deduplication.
- Shell init restores ordinary caller options after normally returning entries,
  makes shell functions available, warns on an ordinary failing entry, and
  continues opening the shell. Tests and documentation do not claim containment
  for `exit`, `exec`, readonly state, or hostile trusted code.
- Persistent-home paths are fingerprinted and NO-PR mode never mounts their
  token-bearing parent directory.
- Devbox-owned Git/`gh` state is ephemeral and mode-specific. A PR-to-NO-PR
  transition leaves no active helper, URL rewrite, saved `gh` credential, or
  modified repository origin, and a direct `docker exec` receives the NO-PR
  askpass-denial environment.
- State keys for repositories that sanitize to the same owner/repo text remain
  distinct, preventing token, fingerprint, or home-directory reuse.
- Build-context staging includes only allowlisted runtime/generated files and
  excludes `secrets`, private keys, project config, and `.run` tokens.
- Concurrent staging/build attempts serialize safely and cannot publish a
  partial context or overwrite another config's image tag. A forced rebuild of
  the same input tag returns and fingerprints the newly inspected image ID.
- Image validity changes when either runtime files or tooling config changes.
- Container fingerprints change when any non-secret container-spec field or the
  keyed secret revision changes. Equivalent spec mappings hash identically, raw
  secret values never appear in state, and changing a previously omitted
  environment field is caught.
- Lifecycle reconciliation recovers from interruption at every journaled rename,
  create, start, verification, fingerprint, and cleanup boundary.
- An unmanaged container or backup with a colliding name is never mutated.
- Refresher ownership is validated beyond a bare PID, and token minting occurs
  after simulated long-running image provisioning.
- Existing PR/NO-PR tests still pass and never expose credentials.

### Docker integration tests

Run these only when Docker is available:

1. Build with the empty config and assert `python`, `opencode`, `node`, `npm`,
   `nvm`, `fvm`, and `flutter` are absent.
2. Assert Bash, Git, and `gh` remain available and the entrypoint behavior is
   unchanged.
3. Build with a harmless apt package and verify it exists.
4. Build with a harmless user command that creates an executable in
   `/opt/devbox/bin`; verify ownership and configured `PATH` as the devbox user.
5. Configure environment, path, and shell-function initialization; verify
   interactive Bash, `bash -lc`, editor attach, and documented direct-exec
   behavior.
6. Start with config A, change to config B, and verify the image rebuilds and a
   stale repository container is recreated.
7. Make a custom command fail and verify the prior working container is not
   removed.
8. Make the candidate entrypoint/startup check fail and verify the renamed
   backup container and fingerprint are restored.
9. Interrupt the replacement flow at each journal phase and verify the next
   start deterministically restores or completes it; verify an unmanaged name
   collision produces an error without mutation.
10. Change `ai.env` and verify the container is recreated using the keyed secret
    revision without placing the secret or revision in image/container labels.
11. Switch a persistent home from PR to NO-PR mode and verify direct
    `docker exec` Git/`gh` commands cannot use PR helpers, saved `gh`
    configuration, or the previous repository rewrite.
12. Verify home caches survive an otherwise-required container recreation, while
   unconfigured changes made only in the container layer do not.
13. Smoke-test pinned nvm/Node/npm, FVM/Flutter, and RVM/Ruby examples, including
    version switching in interactive and non-interactive Bash.
14. Test an attached shell under the Docker Desktop and native-Linux user models
   where CI coverage is available.

## Migration and compatibility

- Removing OpenCode from `runtime/Dockerfile` changes the runtime hash. On the
  next `devbox start`, devbox will build the clean image and recreate that
  repository's stale container using the new image ID.
- Existing repository files remain safe because `/workspace` is a bind mount.
  Tool caches or global packages stored only inside the old container will not
  carry over and must be represented in `devbox.toml` if they are required.
  The first migration cannot recover the old container's unmounted home. State
  written below the new persistent `/home/devbox` mount survives subsequent
  container recreation.
- Users who rely on OpenCode should add its opt-in build command before their
  first start after upgrading.
- `secrets/ai.env` remains compatible and continues to be injected at runtime.
- Do not automatically prune old Docker images; mention manual cleanup in the
  migration notes instead of deleting user data implicitly.

## Future per-project configuration

After the central configuration is stable, add optional lookup of:

```text
<target-repo>/.devbox/devbox.toml
```

That file is distinct from the standalone devbox installation, despite the
shared directory name. The future design should define explicit precedence and
trust behavior before executing repository-provided build commands. A sensible
starting policy is:

1. central `<devbox-home>/devbox.toml` supplies owner-approved defaults;
2. project config is disabled unless the owner opts into project config;
3. devbox shows the project config path and digest before first use or after a
   digest change;
4. the effective config has documented merge rules (prefer array replacement
   over surprising implicit concatenation); and
5. the isolated build context still excludes the repository except for files
   deliberately approved as provisioning inputs.

This trust step matters because an agent can edit the mounted repository. It
must not be able to silently add a build command that is accepted on the next
host-side `devbox start`.

## Recommended implementation order

1. Apply the confirmed clean-runtime boundary and config extensibility decisions
   below.
2. Add the TOML model, environment/path validation, hashing, and unit tests.
3. Add the stable runtime user, persistent-home mount, and explicit
   `/opt/devbox` ownership model.
4. Remove OpenCode from the default Dockerfile.
5. Generate separate root/user provisioning and safe shell-init build inputs.
6. Make build staging, input-addressed images, and container replacement
   atomic and rollback-safe.
7. Make image validation and container fingerprints config-aware.
8. Wire the config and runtime environment into `devbox start`.
9. Add `devbox config check`.
10. Run unit and Docker integration tests, including version-manager smoke tests.
11. Add tested opt-in examples and migration documentation.
12. Defer target-repository config lookup to a separate follow-up change.

## Confirmed phase-one decisions

1. **Clean-runtime boundary:** retain Git and `gh` as internal devbox
   dependencies. Python and user/project tooling remain optional.
2. **Extensibility:** accept trusted arbitrary root/user build commands, with
   tested examples rather than a curated installer registry.
3. **Version control:** commit the central `devbox.toml` so one installation is
   portable and its declared inputs are reconstructible. This is intentionally
   installation-owner rather than per-OS-user configuration in phase one.

The first implementation reads only `<devbox-home>/devbox.toml`;
target-repository and shared-installation per-user configuration remain
deliberate follow-ups.

## Version-manager references

- nvm documents `NVM_DIR`, sourcing `nvm.sh`, and `BASH_ENV` for Docker and
  non-interactive Bash: <https://github.com/nvm-sh/nvm>
- FVM documents its custom install directory, PATH setup, and writable
  `FVM_CACHE_PATH`: <https://fvm.app/documentation/getting-started/installation>
  and <https://fvm.app/documentation/getting-started/configuration>
- RVM documents user versus system installs and why `scripts/rvm` must be sourced
  as a function to switch the current environment:
  <https://rvm.io/rvm/install> and <https://rvm.io/workflow/scripting>

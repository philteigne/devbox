# devbox Launch Configuration Spec

This spec defines host-side launch customization for devbox containers.

The authoritative launch configuration is stored inside `.devbox/`, not inside
the target repository. The target repository continues to be mounted read/write
at `/workspace`, but it does not control the container image or launch policy by
default.

## 1. Goals

- Let a user customize what a repo devbox launches with: packages, tools,
  environment defaults, ports, and the long-running command.
- Keep the authoritative config outside the agent-editable target repo.
- Preserve the current trusted base runtime image behavior.
- Rebuild/recreate containers automatically when launch config changes.
- Keep the first version declarative and allowlisted. Do not support arbitrary
  shell hooks or raw Dockerfile fragments in the initial version.

## 2. Non-Goals

- Do not read `devbox.yml` from the target repo automatically.
- Do not allow launch config to mount arbitrary host paths.
- Do not support privileged containers, host networking, Docker socket mounts,
  custom entrypoints, or arbitrary `RUN` shell in version one.
- Do not store secrets in `launch.yml`.

## 3. File Location

For a target repo resolved as `<owner>/<repo>`, store launch config at:

```text
.devbox/config/<owner>/<repo>/launch.yml
```

The existing repo metadata remains at:

```text
.devbox/config/<owner>/<repo>/config.env
```

If `launch.yml` does not exist, devbox uses default launch behavior equivalent
to the current app behavior.

## 4. Example

```yaml
version: 1

tools:
  opencode: true

apt:
  - ripgrep
  - jq

env:
  OPENCODE_MODEL: opencode/claude-sonnet-4-5

ports:
  - 3000
  - 5173

command:
  - sleep
  - infinity
```

## 5. Schema

The file is YAML. Unknown top-level keys are errors.

### 5.1 `version`

Required integer.

Only supported value:

```yaml
version: 1
```

### 5.2 `tools`

Optional object.

Supported keys:

```yaml
tools:
  opencode: true
```

Rules:

- `opencode` is boolean.
- In the current implementation, opencode is already installed in the base
  image. `opencode: true` is therefore informational/explicit for now.
- `opencode: false` must not remove opencode from the base image in version one.
  It means devbox should not auto-start opencode-specific serve behavior if that
  is added later.

### 5.3 `apt`

Optional list of Debian package names to install into a repo-specific image.

Example:

```yaml
apt:
  - ripgrep
  - jq
```

Validation:

- Each item must be a string.
- Package names must match:

```text
^[a-zA-Z0-9][a-zA-Z0-9+_.-]*$
```

- Empty strings are invalid.
- Duplicates should be removed after validation while preserving first-seen
  order.

### 5.4 `env`

Optional object of default environment variables passed to the container.

Example:

```yaml
env:
  OPENCODE_MODEL: opencode/claude-sonnet-4-5
  NODE_ENV: development
```

Validation:

- Keys must match:

```text
^[A-Za-z_][A-Za-z0-9_]*$
```

- Values must be strings, integers, floats, booleans, or null.
- Values are converted to strings before being passed to Docker.
- Null values mean "omit this variable".

Precedence:

1. Built-in devbox env from `start.py`.
2. Values from `launch.yml`.
3. Secrets loaded from `.devbox/secrets/ai.env`.

This means secrets win over `launch.yml` when keys overlap.

### 5.5 `ports`

Optional list of container ports to publish.

Example:

```yaml
ports:
  - 3000
  - 5173
```

Validation:

- Each port must be an integer from `1` to `65535`.
- Duplicates should be removed while preserving order.

Initial publish behavior:

- Publish each container port to the same host port with Docker `-p`.
- If Docker fails because the host port is already in use, surface the Docker
  error clearly.

Later versions may add automatic host port allocation, but this spec keeps
version one predictable.

### 5.6 `command`

Optional list of strings used as the container command.

Default:

```yaml
command:
  - sleep
  - infinity
```

Validation:

- Must be a non-empty list of non-empty strings.
- Must not be interpreted by a shell.
- Pass directly to Docker as argv after the image name.

## 6. Image Model

Keep the current base image:

```text
devbox-base
```

For repos with launch config that affects the image, build a derived image:

```text
devbox-<owner>-<repo>-runtime-<hash>
```

The hash is computed from the normalized launch config plus the current base
image id. Use the existing `docker.sanitize()` helper for owner/repo name
components.

If no image-affecting fields are present, devbox may use `devbox-base` directly.
In version one, the only image-affecting field is:

```text
apt
```

## 7. Generated Dockerfile

Generate derived Dockerfiles under `.run/<owner>-<repo>/build/`.

Example path:

```text
.run/<owner>-<repo>/build/Dockerfile
```

For the example config above, generate:

```dockerfile
FROM devbox-base

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ripgrep \
        jq \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
```

Rules:

- Only generate Dockerfile instructions from validated structured fields.
- Do not copy the target repo into the image.
- Do not include secrets in the generated Dockerfile.
- Do not include arbitrary user-provided shell.

## 8. Container Creation Changes

Update `devbox.core.docker.create_container()` to accept:

```python
image_name: str
ports: list[int]
command: list[str]
```

The Docker run command should:

- Continue mounting the target repo at `/workspace`.
- Continue setting `-w /workspace`.
- Continue mounting `/devbox-run` only in PR mode.
- Continue passing environment variables with `-e`.
- Add `-p <port>:<port>` for each configured port.
- Use the selected image name instead of the global `IMAGE_NAME`.
- Append the configured command instead of hard-coded `sleep infinity`.

The current behavior should remain identical when no `launch.yml` exists.

## 9. Fingerprint Changes

Update the container fingerprint to include:

- selected image id
- normalized launch config hash
- published ports
- command argv
- launch config file hash

If `launch.yml` changes, the existing container should be considered stale and
recreated.

## 10. Config Loading Module

Add a module:

```text
devbox/core/launch_config.py
```

Responsibilities:

- Resolve `launch.yml` path for owner/repo.
- Read YAML.
- Validate schema.
- Normalize defaults.
- Expose a dataclass.
- Compute a stable normalized hash.

Suggested dataclass:

```python
@dataclass(frozen=True)
class LaunchConfig:
    version: int
    tools: dict[str, bool]
    apt: tuple[str, ...]
    env: dict[str, str]
    ports: tuple[int, ...]
    command: tuple[str, ...]
    path: Path | None
    source_hash: str
```

Default config:

```python
LaunchConfig(
    version=1,
    tools={"opencode": True},
    apt=(),
    env={},
    ports=(),
    command=("sleep", "infinity"),
    path=None,
    source_hash="",
)
```

## 11. CLI UX

Version one can work without adding a new command. `devbox start` should
automatically use `.devbox/config/<owner>/<repo>/launch.yml` if present.

Recommended follow-up command:

```text
devbox configure [path]
```

This can create a starter `launch.yml` for the resolved repo.

Starter file:

```yaml
version: 1

tools:
  opencode: true

apt: []

env: {}

ports: []

command:
  - sleep
  - infinity
```

## 12. Error Handling

Invalid launch config should fail before any Docker build or container removal.

Examples:

```text
error: invalid launch config `.devbox/config/owner/repo/launch.yml`: unknown key `mounts`
error: invalid launch config `.devbox/config/owner/repo/launch.yml`: apt[0] contains invalid package name `curl && whoami`
error: invalid launch config `.devbox/config/owner/repo/launch.yml`: command must be a non-empty list of strings
```

Errors should use `DevboxError` so the CLI reports them consistently.

## 13. Security Notes

- The target repo is mounted read/write and is agent-editable.
- Because `launch.yml` lives in `.devbox/config/...`, the running agent cannot
  modify authoritative launch config unless `.devbox/` overlaps the target repo.
- The existing containment guard in `start.py` already refuses overlap between
  `.devbox/` and the target repo. Keep that invariant.
- `launch.yml` is still local code/config. A user should only add packages and
  ports they intend to trust.
- Do not mount the Docker socket into containers.
- Do not support raw shell in version one.

## 14. Implementation Steps

1. Add `PyYAML` to `requirements.txt`, or implement strict JSON-compatible YAML
   parsing if avoiding a dependency. Prefer `yaml.safe_load`.
2. Add `devbox/core/launch_config.py`.
3. Add tests for missing config, valid config, unknown keys, invalid package
   names, invalid env keys, invalid ports, and invalid command values.
4. Add Docker helpers to build a derived image when `apt` is non-empty.
5. Update `start.py` to load launch config before image selection.
6. Merge env with the precedence defined in section 5.4.
7. Update `create_container()` to accept selected image, ports, and command.
8. Update fingerprinting so config changes recreate containers.
9. Run the test suite.

## 15. Acceptance Criteria

- With no `launch.yml`, `devbox start` behaves the same as it does today.
- With valid `apt` entries, devbox builds a repo-specific derived image.
- With valid `env`, variables are passed into the container, with
  `.devbox/secrets/ai.env` taking precedence.
- With valid `ports`, Docker publishes those ports.
- With valid `command`, Docker uses that argv as the container command.
- Invalid config fails before removing or recreating an existing container.
- Editing a target repo file cannot change launch behavior unless the user also
  edits host-side `.devbox/config/<owner>/<repo>/launch.yml`.

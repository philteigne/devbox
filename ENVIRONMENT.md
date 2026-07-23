# Environment files

Devbox does not load a root `.env` file. It reads the following purpose-specific
files relative to this repository (the devbox installation), not relative to a
target repository:

| File | Secret? | Created by | Purpose |
| --- | --- | --- | --- |
| `app/app.env` | No | User; later completed by `devbox init` | Identifies the GitHub App and its bot commit identity. |
| `app/*.pem` | **Yes** | User | The GitHub App private key. This is not an env file, but is required for PR mode. |
| `secrets/ai.env` | **Yes** | User | Variables injected into every target-repository container. |
| `secrets/gh.env` | **Yes** | User, optionally | A classic GitHub PAT used only to add a repository to a selected-repositories App installation. |
| `config/<owner>/<repo>/config.env` | No | `devbox init` | Cached repository identity and PR-mode safety state. |

Examples are provided at:

- `app/app.env.example`
- `secrets/ai.env.example`
- `secrets/gh.env.example`
- `config/config.env.example`

Copy only the examples you need and remove the `.example` suffix. Do not copy
`config/config.env.example` directly: run `python -m devbox init <repo-path>` so
Devbox can query GitHub and write the correctly nested file.

## File format

These files use a small dotenv-like format:

```env
# Blank lines and full-line comments are ignored.
NAME=value
QUOTED_VALUE="quotes around the whole value are removed"
INLINE_COMMENT=value # a comment starts at a space followed by #
```

- Use one `NAME=value` assignment per line; `export NAME=value` is not supported.
- Variable expansion and escape sequences are not evaluated.
- Empty values are accepted, though most required settings must be non-empty.
- Keep real credentials only in ignored files (`secrets/*.env` and `app/*.pem`).
  The committed `.env.example` files must contain placeholders only.

## Runtime precedence

Container variables are applied in this order:

1. Devbox built-ins such as `OWNER`, `REPO`, and `MODE`
2. The target repository's host-side launch configuration
3. Non-empty values from `secrets/ai.env`

Later values win. Consequently, an AI secret can override a launch-config or
built-in variable with the same name. Treat every non-empty entry in
`secrets/ai.env` as container-visible secret material.

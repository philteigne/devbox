#!/usr/bin/env bash
set -euo pipefail

export HOME="${HOME:-/tmp/devbox-home}"
if [ "$HOME" = "/root" ] && [ "$(id -u)" != "0" ]; then
  export HOME=/tmp/devbox-home
fi
mkdir -p "$HOME"

git config --global user.name "${GIT_USER_NAME:-devbox-local}"
git config --global user.email "${GIT_USER_EMAIL:-devbox-local@example.invalid}"
git config --global --add safe.directory /workspace

if [ "${MODE:-NO-PR}" = "PR" ]; then
  git config --global credential.helper "/usr/local/bin/git-credential-devbox"
  git config --global url."https://github.com/".insteadOf "git@github.com:"
  git config --global url."https://github.com/".insteadOf "ssh://git@github.com/"
  if [ -n "${OWNER:-}" ] && [ -n "${REPO:-}" ] && git -C /workspace remote get-url origin >/dev/null 2>&1; then
    git -C /workspace remote set-url origin "https://github.com/${OWNER}/${REPO}.git"
  fi
else
  git config --global --unset-all credential.helper >/dev/null 2>&1 || true
  cat >/tmp/devbox-askpass-fail <<'EOF'
#!/usr/bin/env bash
echo "devbox NO-PR mode has no GitHub credentials" >&2
exit 1
EOF
  chmod +x /tmp/devbox-askpass-fail
  export GIT_ASKPASS=/tmp/devbox-askpass-fail
  export SSH_ASKPASS=/tmp/devbox-askpass-fail
  export GIT_TERMINAL_PROMPT=0
fi

exec "$@"

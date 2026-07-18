#!/usr/bin/env bash
set -euo pipefail

if [ -r /devbox-run/token ]; then
  export GH_TOKEN
  GH_TOKEN="$(cat /devbox-run/token)"
fi

if [ -x /usr/bin/gh ]; then
  exec /usr/bin/gh "$@"
fi

if [ -x /usr/local/bin/gh.real ]; then
  exec /usr/local/bin/gh.real "$@"
fi

echo "real gh binary not found" >&2
exit 127


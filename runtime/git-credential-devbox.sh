#!/usr/bin/env bash
set -euo pipefail

operation="${1:-}"
[ "$operation" = "get" ] || exit 0

protocol=""
host=""
while IFS='=' read -r key value; do
  [ -n "$key" ] || break
  case "$key" in
    protocol) protocol="$value" ;;
    host) host="$value" ;;
  esac
done

if [ "$protocol" = "https" ] && [ "$host" = "github.com" ] && [ -r /devbox-run/token ]; then
  token="$(cat /devbox-run/token)"
  if [ -n "$token" ]; then
    printf 'username=x-access-token\n'
    printf 'password=%s\n' "$token"
  fi
fi


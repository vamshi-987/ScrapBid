#!/bin/sh
set -e

# Reinstall dependencies only when package-lock.json has changed since the last install.
if [ -f package-lock.json ]; then
  wanted="$(sha256sum package-lock.json | cut -d' ' -f1)"
  installed="$(cat node_modules/.lock-sha256 2>/dev/null || true)"
  if [ "$wanted" != "$installed" ]; then
    echo "Installing frontend dependencies..."
    npm ci --no-audit --no-fund
    echo "$wanted" > node_modules/.lock-sha256
  fi
fi

exec "$@"

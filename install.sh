#!/usr/bin/env bash
# Copyright (c) 2026 Uptrack contributors
# SPDX-License-Identifier: MIT
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_BIN="${HOME}/.local/bin/uptrack"
STATE_DIR="${HOME}/.local/share/uptrack"
CACHE_DIR="${HOME}/.cache/uptrack"

mkdir -p "${HOME}/.local/bin" "${STATE_DIR}" "${CACHE_DIR}"
chmod +x "${ROOT_DIR}/uptrack.py" "${ROOT_DIR}/tracker.py"

ln -sf "${ROOT_DIR}/uptrack.py" "${TARGET_BIN}"

echo "Installed launcher: ${TARGET_BIN} -> ${ROOT_DIR}/uptrack.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[error] python3 not found" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "[warn] curl not found; lore feed checks may fail"
fi

if ! command -v b4 >/dev/null 2>&1 && [[ ! -x /usr2/naala/bin/b4 ]]; then
  echo "[warn] b4 not found in PATH (or /usr2/naala/bin/b4); 'am' subcommand will fail"
fi

python3 - <<'PY'
import sys
print(f"Python: {sys.version.split()[0]}")
try:
    import tomllib  # noqa
    print("TOML loader: tomllib")
except Exception:
    try:
        import tomli  # noqa
        print("TOML loader: tomli")
    except Exception:
        print("TOML loader: minimal fallback parser in uptrack.py")
PY

echo
echo "Run: ${TARGET_BIN} --help"
echo "If ~/.local/bin is not on PATH, add: export PATH=\"$HOME/.local/bin:$PATH\""

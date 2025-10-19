#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import importlib
m = importlib.import_module("speed_friending_matcher.server.server")
assert hasattr(m, "app"), "No app in server"
print("import OK")
PY
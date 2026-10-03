#!/usr/bin/env bash
# Launch only from the operator-coordinated GUI terminal so its DISPLAY is inherited.
set -euo pipefail
: "${DISPLAY:?Run this in the existing authorized cloud GUI terminal}"
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PARAVIEW_VALIDATION_PYTHON:-python}"
OUT="${PARAVIEW_RENDER_WORKSPACE:-$(mktemp -d "$ROOT/render-acceptance-XXXXXX")}"
mkdir -p "$OUT"
export PARAVIEW_RENDER_ACCEPTANCE=1 PARAVIEW_RENDER_WORKSPACE="$OUT" PARAVIEW_RENDER_EVIDENCE="$OUT/evidence.json"
if [[ "${PARAVIEW_VALIDATE_INSTALLED:-0}" == 1 ]]; then
  unset PYTHONPATH
else
  export PYTHONPATH="$ROOT/src"
fi
cd "$ROOT"
"$PYTHON" -c 'import os,dcc_mcp_paraview; p=dcc_mcp_paraview.__file__; print("Adapter origin:", p); assert os.environ.get("PARAVIEW_VALIDATE_INSTALLED") != "1" or "site-packages" in p' > "$OUT/test.log" 2>&1
set +e
"$PYTHON" -m pytest -q -o pythonpath= tests/test_render_mcp.py >> "$OUT/test.log" 2>&1
STATUS=$?
set -e
printf '%s\n' "$STATUS" > "$OUT/exit-code.txt"
printf 'Render acceptance: exit=%s artifacts=%s\n' "$STATUS" "$OUT"
exit "$STATUS"

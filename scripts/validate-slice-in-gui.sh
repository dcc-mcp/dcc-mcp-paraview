#!/usr/bin/env bash
# Proposed opt-in qualification. The caller must already own the isolated GUI lane.
set -euo pipefail
: "${DISPLAY:?Use the operator-coordinated isolated graphical terminal}"
: "${PARAVIEW_SLICE_WORKSPACE:?Choose a new empty artifact directory}"
command -v pvpython >/dev/null
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PARAVIEW_VALIDATION_PYTHON:-python}"
export PARAVIEW_SLICE_ACCEPTANCE=1 DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS=1
: "${DCC_MCP_LOG_DIR:?Choose an operator-owned writable log directory}"
mkdir -p "$DCC_MCP_LOG_DIR"
if [[ "${PARAVIEW_VALIDATE_INSTALLED:-0}" == 1 ]]; then
  unset PYTHONPATH
else
  export PYTHONPATH="$ROOT/src"
fi
cd "$ROOT"
"$PYTHON" -c 'import os,dcc_mcp_paraview; p=dcc_mcp_paraview.__file__; print("Adapter origin:", p); assert os.environ.get("PARAVIEW_VALIDATE_INSTALLED") != "1" or "site-packages" in p'
TEST_ROOT="$(mktemp -d "${DCC_MCP_LOG_DIR%/}/slice-tests-XXXXXX")"
"$PYTHON" -m pytest --strict-markers -q -o pythonpath= --basetemp "$TEST_ROOT" tests/test_slice_render_mcp.py

#!/usr/bin/env bash
# Clean installed-wheel acceptance; caller may select -m 'not paraview' for portable CI.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python}"
OUT="${WHEEL_VALIDATION_ROOT:-$(mktemp -d)}"
mkdir -p "$OUT/dist"
"$PYTHON" -m build "$ROOT" --outdir "$OUT/dist"
"$PYTHON" -m venv "$OUT/venv"
"$OUT/venv/bin/python" -m pip install "$OUT"/dist/*.whl pytest jsonschema 'mcp==1.30.0'
cp -R "$ROOT/tests" "$OUT/tests"
cp "$ROOT/pyproject.toml" "$OUT/pyproject.toml"
cd "$OUT"
unset PYTHONPATH
"$OUT/venv/bin/python" -c 'import dcc_mcp_paraview; from pathlib import Path; p=Path(dcc_mcp_paraview.__file__); print("Installed origin:", p); assert "site-packages" in p.parts'
"$OUT/venv/bin/python" -m pytest --strict-markers -q -o pythonpath= "$OUT/tests" "$@"

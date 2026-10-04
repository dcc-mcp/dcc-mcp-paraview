#!/usr/bin/env bash
# Opt-in follow-on proof; caller owns the existing isolated graphical lane.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PARAVIEW_PRESENTATION_CONTROLS_ACCEPTANCE=1
exec "$ROOT/scripts/validate-slice-in-gui.sh"

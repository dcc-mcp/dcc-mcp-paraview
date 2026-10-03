# Manual installation runbook

**Pre-release manual lifecycle only.** The standard automated Install SOP
install/status/verify/uninstall/upgrade surface, receipts and host-complete
transactions remain unimplemented. This runbook is the canonical repository
installation path; it intentionally has no Install SOP conformance marker or
released artifact URL. Do not run an invented automated installer command.


This initial adapter uses an ordinary isolated Python environment. It does not implement or claim the organization-wide automated Install SOP receipt/rollback lifecycle; no custom install-report schema is invented. Automated `plan -> execute -> verify -> status -> uninstall` integration is a remaining deployment task, and package installation alone is not live-host readiness.

## Plan

Select Linux, a writable operator-owned workspace, Python 3.10–3.12 for the adapter (historical 0.20.39 native acceptance used 3.12.14), and an existing official ParaView 5.13.2 distribution exposing `pvpython`. Do not install Core into vendor Python or change an existing ParaView session. Review that imported datasets are trusted native files and that the sidecar needs only loopback network access.

## Execute

```sh
python -m venv .venv
.venv/bin/python -m pip install .
mkdir -p artifacts
.venv/bin/dcc-mcp-paraview --workspace "$PWD/artifacts" --pvpython /path/to/pvpython
```

Core and server 0.20.41 are the exact candidate dependency pins; the adapter rejects mismatches. This upgrade requires fresh Linux native and MCP transport acceptance. Core/server 0.20.39 evidence remains historical. The official CLI 0.20.41 can be installed into the separate operator environment when needed; it is not an adapter dependency. A workspace must exist before startup. The printed endpoint is a readiness claim only after the child handshake and Core listener have started. `--port 0` preserves OS assignment. Use a dedicated writable environment and test artifact directory. Tests isolate Core registry/config/data/cache state and set DCC_MCP_DISABLE_DEFAULT_SKILL_PATHS=1; avoid changing global environment or user directories.

## Verify and status

Discover/load `paraview-pipeline`, then call `get_adapter_info` and `inspect_pipeline`. The latter reports the vendor version and exact main-thread identity. Perform a bounded synthetic sphere/clip/export roundtrip in a fresh workspace. `tests/test_mcp.py` is the executable acceptance recipe, using official SDK 1.30.0 and real HTTP transport. Poll existing Core jobs after async launches. No package-only check substitutes for this test.

## Upgrade and rollback

Stop the adapter first; export any wanted data/state before stopping. Install a new package into a separate environment, repeat the smoke in a fresh workspace, then switch your MCP endpoint configuration. If validation fails, stop the new process and reuse the previous environment. This manual separation preserves the previous environment; automated install replacement rollback is not implemented. For the 0.20.41 candidate, retain the exact 0.20.39 source/environment described in [the upgrade record](docs/RUNTIME_UPGRADE_0_20_41.md).

## Uninstall

Stop the adapter with Ctrl+C or SIGTERM and verify its listener and registry entry are gone. Run `.venv/bin/python -m pip uninstall dcc-mcp-paraview` or remove only the dedicated environment after reviewing its contents. Preserve project outputs unless you separately choose to delete them. The adapter does not install a GUI startup hook or modify ParaView configuration.

# Validation and qualification

## Qualified profile, 2026-10-02

- Linux; sidecar Python 3.12.14; ParaView 5.13.2 with vendor Python 3.13.5
- Released `dcc-mcp-core` and `dcc-mcp-server` **0.20.39**, official MCP SDK **1.30.0**, negotiated protocol **2025-06-18**
- Package requires sidecar Python 3.10–3.12; portable CI is configured for 3.10/3.12. Local native acceptance is on 3.12.14 only
- Core/server and native host versions are checked before admitting work. This is a narrow measured qualification, not a claim about other versions or operating systems
- Display-backed rendering uses the supported `pvpython --force-offscreen-rendering` launch option and an existing operator-provided DISPLAY. No Xvfb, desktop capture, resize fallback, display reconfiguration or open-GUI-scene mutation is used

## Final source and installed-code checks

- Ruff lint and formatting: pass
- Source suite: **68 passed, 1 skipped**; only the opt-in DISPLAY render test skips in the ordinary shell lane
- Portable suite (`-m 'not paraview'`): **59 passed, 10 deselected**. This includes real Core HTTP/official SDK tests against a controlled protocol host, not native ParaView acceptance
- Fresh installed wheel: **68 passed, 1 skipped**, copied tests run outside the source tree, PYTHONPATH unset, pytest source-path injection disabled, imported module verified in the new environment's site-packages
- After standard uninstall/reinstall lifecycle rehearsal: **68 passed, 1 skipped**, again from the installed wheel
- Actual installable skill: Core `validate_skill` clean, no issues
- Source-display acceptance: **1 passed**, including native import, contour, scalar render, PVSM save/reopen and rerender; see [source render evidence](render-source-evidence.json)
- Post-lifecycle installed-wheel DISPLAY acceptance: **1 passed**, with independently verified site-packages origin and the same native PNG/PVSM assertions; see [installed render evidence](render-installed-evidence.json)

[Source test log](hardened-test.log), [portable test log](portable-test.log), [real MCP evidence](hardened-mcp-evidence.json), [lint](lint-check.log), and [format](format-check.log) are local artifacts. CI workflows are configured; no remote CI run or public release is claimed.

## Rendering evidence

The isolated native host imports a synthetic single-file VTI, extracts contours at 4 and 6, and renders 320×240 pixels with the Viridis preset, range [0, 10], camera position [9, 7, 6], target [0, 0, 0] and background [0.1, 0.15, 0.2]. Native ParaView reads back the camera, representation enum, color-array selection, range and transfer-function points. Native VTK decodes the PNG, checks exact dimensions and nonflat RGB pixels. The preview was also inspected visually.

The native PVSM is saved and reopened in the same trusted session, then rendered again. The two PNGs are byte-identical. Evidence confirms loopback binding and host/pump/server shutdown. Presence of DISPLAY alone is explicitly reported as a prerequisite, not successful context creation.

This live acceptance found and fixed two real defects: enum properties require native enum-value readback, and a compositor-managed visible render window could ignore requested screenshot geometry. Explicit image resolution plus ParaView's supported offscreen launch flag preserve requested dimensions without image resampling.

Run from an authorized isolated GUI terminal:

```sh
PARAVIEW_VALIDATION_PYTHON=/path/to/sidecar/python scripts/validate-render-in-gui.sh
# To verify an installed wheel, without source imports:
PARAVIEW_VALIDATE_INSTALLED=1 PARAVIEW_VALIDATION_PYTHON=/path/to/wheel-env/bin/python scripts/validate-render-in-gui.sh
```

Every run creates a fresh output directory, writes a log, exit code, PNGs, PVSM and evidence, then shuts down its own server and host. It never loads or edits an existing GUI scene.

## IPC, lifecycle and cancellation regressions

- Closed typed input validation rejects wrong types, null bytes, huge/nonfinite numeric values, unknown keys, unsupported presets and degenerate camera vectors before launch
- Readiness checks protocol and version. Frames reject malformed JSON, duplicate keys, nonfinite values, oversized payloads, stale request/job IDs, conflicting result/error fields and malformed domain/error shapes
- Both input and output frames are bounded. Nonblocking request writes and response waits check cancellation/deadline; startup diagnostics have a separate 64 KiB bound and are forbidden after readiness
- Concurrent Core job contexts preserve unique request IDs and job identities; independent sessions cannot see each other's pipelines or reopen each other's native state
- Controlled host tests prove timeout, cancellation and active stop prevent delayed mutation. Owned descendant processes are also killed if their launcher exits
- Direct Python dispatcher timeout calls Core's public cancellation API; later queue drain cannot execute abandoned work, and active owned host work is terminated
- Real Core HTTP jobs launched once through the official SDK preserve the returned job ID. Core's documented `DELETE /v1/jobs/{id}` cancellation route reaches the adapter's cancellation probe, produces terminal job state, terminates the controlled host, and prevents its delayed mutation. This is controlled-host transport evidence, not a claim of cooperative interruption inside ParaView C++
- Constructor/start failure, repeated stop and fresh-instance restart tests verify host, pipe and pump cleanup
- Loopback binding is inspected through Linux socket state; native MCP shutdown confirms closed listener and removed registry entry
- File tests preserve concurrently created destinations, remove failed staging files, reject oversized input and reject all symlink components, including in-root aliases and dangling links

Native calls remain monolithic. Termination discards unsaved state and cannot roll back an artifact already published. A stopped/interrupted server object cannot restart; create a new isolated instance. No cross-restart job recovery is claimed.

## Standard package lifecycle rehearsal

[Package lifecycle evidence](package-lifecycle-evidence.json) records standard uv commands in a fresh dedicated environment:

1. Install the frozen baseline local wheel
2. Explicitly reinstall-upgrade to the hardened 0.1.0 wheel and verify version, module origin and every installed package-file hash against wheel contents
3. Attempt a corrupted-wheel upgrade; require failure and byte-identical installed package files afterward
4. Uninstall; verify import is unavailable from clean isolated Python/cwd
5. Reinstall the final wheel; verify hashes and rerun native/SDK tests

This demonstrates ordinary Python package replacement, failed-install preservation, uninstall and reinstall. It is separate from the organization's shared Install SOP receipt/status/rollback infrastructure and does not claim that integration is implemented here.

## Remaining boundaries

Windows/macOS, Python 3.7/3.9 LTS, other Core or ParaView versions, headless EGL/OSMesa, arbitrary external PVSM, distributed MPI, shared GUI attachment, hostile-concurrent filesystem protection and cross-restart recovery are unqualified. Inputs remain trusted bounded native datasets. Shared installer/catalog lifecycle and any publication are owned separately; no credentials, host upgrades, OS packages or public releases were involved.

# Bounded ImageData: actual Linux HTTP qualification

This qualification executed source commit `9100157ebc60bc30e8f49fce0ce887058377059e`, not the later documentation commit. The candidate wheel SHA256 is `f11071f03ad382cac491a60e7c243fcac0a40f5c2be2452523dda05d80f21a26`; all 27 package members were checked against frozen source and installed bytes.

## Runtime and results

- Linux x86_64; sidecar Python 3.12.14; Core and server **0.20.41**; MCP SDK 1.30.0; native ParaView 5.13.2 with its own Python 3.13.
- Exact installed-wheel host-free suite: **329 passed, 22 native tests deselected**. This result is separate from the actual HTTP runs below.
- Real SDK initialize, skill discovery/load, paginated tool discovery, and actual typed calls. No arbitrary Python/script execution tool was introduced.
- A 343-point grid completed create → save → reopen → contour → slice, including reader-backed state and geometry checks.
- Exactly one 510,705-point creation over HTTP produced two Float64 fields. Every value matched the independent arithmetic oracle, with unrounded delay used to calculate time and each final field independently quantized to ten significant digits. The resulting 13,302,837-byte VTI also matched SHA256 `43ade20b1f2b78149e7d948894568b599db55f5aa8f9a5ceb62050e970e93d9d`.
- The new execution reproduces historical input bytes. It does **not** establish the historical input's original execution route.

## Native lifecycle qualification

A separate fresh workspace, using the same wheel and runtime, performed:

1. One eight-point typed creation, preserving its artifact hash.
2. A schema-valid `1/x` field at x=0. The native operation returned `success=false`, `error=invalid_scalar`; Core reported the completed tool job, not a successful native operation. No failed output or staging file remained, and the eight-point artifact was unchanged.
3. One bounded 1,000,000-point qualification-only request. After its real native staging file was observed, one Core cancellation returned HTTP 204. The same job reached `cancelled`; its stages were removed, the retained artifact was unchanged, and its owned native process exited. No full-data regeneration or retry was performed.

Both runs verified owned process-group, dispatcher and loopback listener termination. The direct-loopback run had no instance ID even at startup. The original full-run caller incorrectly emitted a vacuous `registry_removed=true`; the accompanying projection explicitly corrects this to **no identified entry observed**, not proof of removal. The lifecycle caller records this limitation directly.

## Scope and evidence

[Selected original records and artifact hashes](image-data-linux-2026-10-20.json) preserve original job IDs and per-line/log hashes. Task-root path prefixes are replaced with placeholders. This is reviewed execution evidence, not a signed or tamper-proof attestation.

The full-run sampled owned-process RSS maximum was 270.77 MiB, not a guaranteed peak bound. Actual runs used the approved preflight and disk reserves. They did not render, run Windows/macOS native tests, or exercise a native directory-replacement race. Directory identity/replacement and failure-injection regression coverage remains host-free; these scopes are not conflated.

The existing optional tests in `tests/test_image_data_native.py` cover native operations and mathematical expectations. The acceptance caller additionally used actual HTTP discovery, Core job polling and a separately recorded cancellation. No case-specific mathematical preset was added to the public tool: callers supply bounded dimensions, coordinates and restricted arithmetic expressions.

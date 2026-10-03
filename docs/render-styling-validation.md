# Typed rendering controls acceptance

This independent revision extends the frozen Linux hardening candidate. It does
not broaden the qualified OS/host profile or enable raw execution.

- Host: ParaView 5.13.2, host Python 3.13.5; sidecar Python 3.12.14
- Published Core/server 0.20.39, official MCP SDK 1.30.0
- Source suite: 83 passed, 1 opt-in display test skipped
- Fresh installed wheel suite: 83 passed, 1 display test skipped
- Source display acceptance: 1 passed
- Installed wheel display acceptance including repeated legend visibility: 1 passed

The display tests use the real native renderer and official MCP transport,
closed schemas, native property readback, exact 320x240 PNG dimensions, decoded
nonflat pixels, and identical PNG bytes after native PVSM save/reopen. Solid
color, scalar coloring, material coefficients, orthographic and perspective
framing, orientation axes and scalar-bar visibility are exercised. A final
solid view after a visible scalar legend matches the original solid PNG,
preventing stale overlay leakage. Owned host, dispatcher and listener cleanup
is asserted. Actual pixels were inspected independently.

The first style experiment exposed a ParaView subtlety: obtaining a missing
scalar bar while reading it creates a visible one. The implementation now
obtains that handle before setting visibility and uses a read-only retained
handle during verification. Those failed experiments were retained outside the
publishable source; they are not counted as passing tests.

Rendering requires an operator-provided working DISPLAY. The child uses
--force-offscreen-rendering with that real display. No private X server,
Windows/macOS rendering or display-less GPU context is qualified here.
These functional acceptance images are not polished showcases.

The preceding full hardening revision carries package lifecycle evidence.
This styling increment repeats fresh-wheel installation and real host checks;
it does not claim a new automated Install SOP or cross-platform acceptance.

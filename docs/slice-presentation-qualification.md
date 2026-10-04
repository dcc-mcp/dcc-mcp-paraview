# Selected Slice and presentation qualification

The supplied source patch was applied to public commit
`4faf7c0a4e447077cf62680cefa19e48315fa373` and independently reproduced the
original candidate tree `61762e29b75e0fda72031f9c25c2c8c4a7d6445e`. The later
qualification documents and links are a documentation overlay; the runtime,
schemas and tests remain byte-identical to that candidate. The development
record in [slice-presentation-candidate.md](slice-presentation-candidate.md)
keeps its earlier pending-qualification wording.

[The public qualification projection](slice-presentation-qualification.json)
is supplied by the source author. It is a curated projection of selected private
checks, rather than a full unmodified trace. Raw logs, traces, native images,
states and dataset bytes are excluded. The author reports the following:

- Source and isolated installed-wheel host-free suites each passed 151 tests,
  with 18 native/graphical tests deselected. An independent source review ran
  91 focused host-free checks, including the revision-2 failure and revision-3
  success for existing property objects without Python attribute accessors.
- One selected installed-wheel native test ran on Linux, ParaView 5.13.2,
  Python 3.12.14, Core/server 0.20.41 and official MCP SDK 1.30.0. It saved and
  reopened five viewpoint states and compared the measured presentation without
  reapplying parameters. All five before/after PNG pairs were byte-identical;
  the SDK trace contained 429 recorded events.
- The analytic section had 49 points and 72 triangles, bounds
  `[0,0,-3,3,-3,3]` and scalar range `[0,18]`. Owned host, pump and listener
  cleanup passed.

The tested author wheel SHA256 is
`24d14dbb2e670f84d7fa79d310255f3a93ab21db81ec58945c7b5eb58ac72d32`.
The projection records the three runtime/schema file hashes, per-view image
hashes and private trace hashes. These author reports were not rerun as native
or GUI acceptance on the publication workstation. Current publication checks
and exact-head CI are recorded separately in the pull request.

[Selected discovered tool declarations](slice-presentation-discovered-schemas.json)
preserve the author's actual SDK tools/list projection for the three added tools.
This projection is not asserted to be byte-identical to the authored YAML
schema: Core may normalize declarations. Both typed host validation and direct
slice validation reject a zero normal before creating the new proxy.

Coverage is the measured bounded Surface presentation subset and this one
selected native test, rather than all 18 deselected tests or every possible
ParaView property. ParaView 5.13.2 does not expose `CameraClippingRange` through
this view property interface; it is explicitly reported unavailable, with no
camera getter or creation fallback. Cancellation and timeout terminate the
owned host, may leave staging files and cannot undo an already published
artifact. No transactional recovery, external PVSM trust, existing GUI
attachment, software release or new license terms are claimed.

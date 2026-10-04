# Selected line and legend qualification

`render_preview` adds bounded line width and optional scalar legend title,
position, length, thickness and font sizes. All new controls are validated before
native access and explicitly requested values receive native readback before and
after rendering. Verification failures prevent image publication. Omitted
controls retain their existing native values.

The accompanying [qualification projection](presentation-controls-qualification.json),
[selected discovered declarations](presentation-controls-discovered-schemas.json)
and [original base relation](presentation-controls-base-relation.json) are copied
verbatim from the source-author handoff. They are selected projections, not full
unmodified logs or traces. Raw traces, data, images and native states are excluded.
Their hashes and reported equality checks are retained; those raw artifacts were
not replayed during publication.

## Source identity and description correction

The handoff patch is based on merged prerequisite commit
`6863d0f705cd8579676542d59a7f0afd52e25827`, tree
`70b9beb5525f74b8eca21dc3deda490d9b7dc4fa`. Applying its SHA256
`add2e75e070feaa42702d61fd92fd845dc1bea7737eeee203982995e87108a9b`
produces frozen handoff tree `e5b4a3f1726c4a729afc177e185edaf48513d9bf`.
At that stage all 51 non-documentation files matched the original native-tested
implementation tree `68a2d09ff6c70ed00066016986ef99253c7f6fbd` by bytes and Git
mode, and the merged prerequisite's five publication-documentation differences
were preserved.

Publication review then corrected the description of `scalar_bar_thickness` in
the candidate documentation, the pipeline skill and the authored tool schema.
The [ParaView 5.13.2 actor definition](https://github.com/Kitware/ParaView/blob/v5.13.2/Remoting/Views/vtkContext2DScalarBarActor.h)
specifies `ScalarBarThickness` in points akin to font size. Rendered pixel extent
depends on the host, font and DPI, so its integer value does not guarantee pixel
thickness. Bounds remain 1–64 and native values/readback are unchanged.

The final publication retains 49 of those 51 non-documentation files unchanged.
The other two files are `SKILL.md` and `tools.yaml`, with only explanatory text
changes. Runtime Python, tests, shell runners, configuration and packaging are
unchanged. The authored schema structure is identical after removing
`description` fields. The historical discovered declaration still contains the
earlier pixel wording and remains an immutable evidence projection, rather than
a declaration of the final corrected description. The original base relation
also describes the frozen handoff, before this clarification and the added
publication documents.

The original tested wheel has SHA256
`b8f01b645683f6eeb91350f2672a9852768bc0a6e65da1452bbeee67ed4117fb`.
New publication builds have their own identities and host-free checks; they are
not described as newly native-tested distributions.

## Recorded source-author checks

The frozen implementation's source and isolated installed-wheel selections each
recorded 196 host-free passes and 18 native/graphical deselections. Independent
review recorded 136 focused host-free passes. These are historical source-author
receipts, separate from the publication's current exact-head CI.

One selected installed-wheel test,
`test_slice_five_saved_presentations_over_real_mcp`, ran with
`PARAVIEW_PRESENTATION_CONTROLS_ACCEPTANCE=1` on Linux, ParaView 5.13.2,
Core/server 0.20.41 and official MCP SDK 1.30.0. Five separately saved states were
reopened with equal measured presentation and five byte-identical PNG pairs.
The RadiusSquared analytic fixture uses `Time (s)` only to exercise the title
property; it does not measure time. No claim is made that all 18 native cases ran.

A separate synthetic production pass used a 510,705-point / 491,520-cell VTI
to render three true sections and two isolines. It reopened five saved states
and captured five byte-identical PNG pairs without reapplying styles. The views
used 720×480 section images, 1060×340 isoline images, illustrative `Time (s)`
legends and 2.5-pixel isolines. This evidence concerns native panel authoring
and state retention. It does not establish final artwork acceptance, measured
seismic data, a PDE solution or scientific validation.

Together the two distinct reported runs contain ten saved-state/capture pairs.
PC8 publication and current PR CI run host-free source and newly built wheel
checks; they do not repeat native, GUI or synthetic production execution.

## Limits

Preservation covers the measured bounded Surface subset, rather than every
ParaView property or plugin state. `CameraClippingRange` is explicitly unavailable
through the supported ParaView 5.13.2 view-property interface. Bounds constrain
resources but do not guarantee title fit or avoid overlap for every viewport.
Composition still needs visual review.

Native timeout/cancellation terminates the owned host, may leave staging files
and cannot undo an already-published artifact. No transactional cancellation or
cross-restart recovery guarantee is introduced. Existing path confinement,
no-overwrite publication and trusted-state restrictions remain in force.

This source publication includes no software release or PyPI upload.

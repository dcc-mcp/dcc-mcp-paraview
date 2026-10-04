# Bounded line and vertical legend controls

This is a separate follow-on candidate based on frozen source tree
`61762e29b75e0fda72031f9c25c2c8c4a7d6445e`. Its delta does not modify the prior
slice/presentation candidate or inherit that candidate's native qualification.

This development record was frozen before selected native qualification. Later
source-author evidence and the publication's description-only correction are
recorded in [the selected qualification](presentation-controls-qualification.md).
The current publication preserves the merged prerequisite's documentation.

`render_preview` accepts the following optional controls. If omitted, it does
not write the corresponding native properties, so existing behavior is retained.

| Input | Bound | Native property |
| --- | --- | --- |
| `line_width` | Finite number 1–8 pixels | Representation `LineWidth` |
| `scalar_bar_title` | 1–80 plain ASCII characters; no control characters, backslash, dollar sign or braces | Legend `Title` |
| `scalar_bar_position` | Two finite normalized coordinates in [0,1] | Legend `Position` |
| `scalar_bar_length` | Finite fraction 0.05–0.9 | Legend `ScalarBarLength` |
| `scalar_bar_thickness` | Integer 1–64 native points, as for font size | Legend `ScalarBarThickness` |
| `scalar_bar_title_font_size` | Integer 6–48 | Legend `TitleFontSize` |
| `scalar_bar_label_font_size` | Integer 6–48 | Legend `LabelFontSize` |

Legend controls require scalar coloring. Explicit position selects native
`WindowLocation="Any Location"` and `Orientation="Vertical"`; those properties
are also verified. If position and length are supplied together, their vertical
extent must fit inside the view. The numeric bounds constrain render resources;
they do not guarantee that every title or label fits every viewport. Visual QA
still decides the composition. No font file, label/range format string, arbitrary
lookup table or general renderer setting is accepted.

ParaView 5.13.2 specifies `ScalarBarThickness` in points akin to font size,
according to the [native actor definition](https://github.com/Kitware/ParaView/blob/v5.13.2/Remoting/Views/vtkContext2DScalarBarActor.h).
Its rendered pixel extent depends on the host, font and DPI; a value is not a
guaranteed pixel thickness. This clarification does not change validation bounds
or native property values.

Native names are verified against ParaView 5.13.2's
[legend proxy definition](https://github.com/Kitware/ParaView/blob/v5.13.2/Remoting/Views/Resources/3dwidgets_remotingviews.xml)
and the prior exact-host read-only property inventory. The existing
[view and representation definitions](https://github.com/Kitware/ParaView/blob/v5.13.2/Remoting/Views/Resources/views_and_representations.xml)
cover representation properties. This is not native execution of this new delta.

All new inputs are validated before any source, view, visibility or legend
mutation. Explicit writes receive native property readback both before and after
screenshot generation; absent, changed or clamped requested properties fail
verification without publication. The existing read-only presentation inventory
already includes line width, legend text, placement, size and fonts, so saved
states and `capture_current_view` compare their retention without reapplying them.

Example additions to an existing scalar-render request:

```json
{
  "line_width": 2,
  "scalar_bar_title": "Time (s)",
  "scalar_bar_position": [0.89, 0.18],
  "scalar_bar_length": 0.6,
  "scalar_bar_thickness": 12,
  "scalar_bar_title_font_size": 16,
  "scalar_bar_label_font_size": 14
}
```

Existing 32-source, 64 MiB file and 64–2048-pixel image limits are unchanged.
Capture still compares the measured bounded Surface subset. Camera clipping range
remains explicitly unavailable on the supported 5.13.2 view-property interface.
Native timeout/cancellation closes the owned session and is not a transactional
publication guarantee; staging files or already-published artifacts may remain.

## Proposed native selection

From the operator-owned isolated graphical lane, use the existing required
`PARAVIEW_VALIDATION_PYTHON`, `PARAVIEW_SLICE_WORKSPACE`, `DCC_MCP_LOG_DIR` and,
for installed code, `PARAVIEW_VALIDATE_INSTALLED=1`, then run:

```sh
scripts/validate-presentation-controls-in-gui.sh
```

This opts into the new control values in the existing five-view test. The runner
checks the returned render values, read-only presentation and unmodified capture
before/after native state reopen. Its synthetic field and title are property-test
inputs, not a claim that the analytic RadiusSquared fixture measures time. The
runner retains the default SDK transport, task-local logging/job/checkpoint
persistence, disabled telemetry, gateway port zero and disabled failover. It saves
actual SDK trace and failure evidence as before. Native execution and visual
qualification were pending when this record was frozen. The later selected
source-author evidence is linked above; the publication workflow does not rerun
native or production graphical checks. No existing GUI is used by tests.

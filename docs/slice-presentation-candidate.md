# Plane sections and presentation-preserving capture candidate

This is the frozen development record from the source patch. Its pending-native
remarks precede the later selected source-author report; see
[the qualification and its limits](slice-presentation-qualification.md).
Publication-host checks do not rerun that native or GUI evidence.

This candidate is based on public main `4faf7c0a4e447077cf62680cefa19e48315fa373`.
It is a new narrow implementation, not a restoration of an earlier prototype.
Native and graphical acceptance have not been run for this candidate. It inherits
neither the historical native qualification nor a claim that state restore avoids
ParaView's first-render camera initialization.

## Tools

- `slice_plane(name, input_name, origin?, normal?)` uses ParaView 5.13.2's native
  `Slice` filter (`Cut` proxy). It selects Plane, sets exactly one zero offset,
  disables crinkle extraction and enables triangulation. The output is a true
  polygonal section, not clipped volume cells. Defaults are origin `[0,0,0]`
  and normal `[1,0,0]`. Inputs are existing named sources only. The adapter checks
  unique names, the existing 32-source session capacity, finite bounded vectors,
  nonzero normal, at most 32 point arrays, and at most 1,000,000 input points and
  cells before constructing the filter. Native output counts have the same caps.
  Empty sections fail. Plane, offsets, crinkle, triangulation, input bindings,
  output data type, bounds and preserved array names/components are read back.
  Failure deletes the new proxy; the prior active source is restored. Native
  termination still discards the entire owned session, as before.
- `inspect_presentation()` reads existing views and their attached representations
  and scalar bars. It uses existing properties, never Show, GetScalarBar,
  GetColorTransferFunction, Render, UpdatePipeline or a create-view helper. Source
  bindings use registered names and output ports, not proxy IDs. It reports
  camera, projection, size, background, material, scalar selection, actual RGB
  control points/range, legend properties and visible source bindings. Palette
  control points are authoritative; no preset label is guessed from them.
  Camera clipping range is read when the existing proxy exposes that property.
  The exact ParaView 5.13.2 RenderView XML does not expose it; inspection explicitly
  reports `camera_clipping_range` in `unavailable_properties` on that host. No
  active-camera getter or creation fallback is used.
  Native values are read from existing property objects rather than assuming each
  XML property name is also exposed as a Python attribute. This includes internal
  transfer-function properties such as `IndexedLookup`; raw native vector and
  enum properties and wrapped scalar-array selection pairs are supported without
  creating or updating a proxy.
  Inventory is bounded to 16 views, 256 representations per view, 4096 elements
  or characters per measured property and 512 KiB of finite JSON.
- `capture_current_view(path)` exports the existing configured current RenderView
  at its existing size (64–2048 pixels per axis). It calls SaveScreenshot without
  assigning properties, resizing, showing sources, rendering first or resetting
  the camera. Existing workspace, no-overwrite and staged atomic publication
  apply. Native decoded pixels must be nonflat RGB and have the existing size.
  The entire measured presentation must compare equal before and after capture.
  If first render changes a restored camera, palette, legend or another measured
  property, `presentation_changed` is returned and the staged image is removed.
  The tool deliberately does not restore or silently correct that changed state.
  Inspect it before deciding how to proceed.

Inspection covers the bounded Surface workflow exposed by this adapter. It is
not a claim to serialize every possible ParaView view/plugin property. Native
calls remain monolithic; cancellation/timeout closes the owned host and the
existing transport never replays the operation. An already-published artifact
may remain, and forced process termination can leave staging files. This is not
transactional recovery across process termination.

Official API references:
[Slice](https://www.paraview.org/paraview-docs/v5.13.2/python/paraview.simple.Slice.html)
and [simple module](https://www.paraview.org/paraview-docs/v5.13.2/python/paraview.simple.html).
The [5.13.2 RenderView definition](https://github.com/Kitware/ParaView/blob/v5.13.2/Remoting/Views/Resources/views_and_representations.xml)
establishes the clipping-property limitation.

## Proposed graphical qualification

Only the operator coordinating an isolated graphical terminal may run:

```sh
PARAVIEW_VALIDATION_PYTHON=/path/to/candidate/python \
PARAVIEW_SLICE_WORKSPACE=/path/to/new-empty-artifact-directory \
DCC_MCP_LOG_DIR=/path/to/writable-private-logs \
scripts/validate-slice-in-gui.sh
```

For installed-wheel qualification also set `PARAVIEW_VALIDATE_INSTALLED=1`.
The runner uses the original analytic `RadiusSquared=x*x+y*y+z*z` VTI fixture.
To use an unchanged original showcase VTI, set `PARAVIEW_SLICE_INPUT` to that
file and `PARAVIEW_SLICE_SCALAR` to its single-component point scalar. It copies
only that bounded input into the fresh artifact workspace and records its hash.
It never operates an already-open GUI session.

The real official-SDK/Core MCP workflow discovers tools with pagination, creates
one true section, saves five independent camera states and compares presentation
before/after state load and unmodified capture. No `render_preview` runs between
restore and capture. A camera reset therefore fails qualification. The generated
PVSM files use package-relative input data. Evidence records exact runtime
versions, the section, each successful presentation and image hash, and owned
host/pump/listener cleanup. Failure evidence keeps `native_qualified=false`.
Source and installed-wheel runs must use distinct empty output directories.
The SDK's default transport preserves the existing proxy configuration. File
logging, job persistence and checkpoint persistence stay enabled in task-local
locations; the runner requires the configured writable log directory and verifies
healthy job storage there. Telemetry remains explicitly disabled. It appends complete actual SDK protocol request and
result models, notifications, errors and every original-job status poll to
`mcp-trace.jsonl`, flushing each event before continuing. Trace sequence numbers
correlate recorded requests/results; they are not claimed to be transport RPC IDs.
These generated traces and runtime evidence are private validation output, not
public project artifacts.

The default analytic fixture is independently checked as 343 input points and
216 cells on `[-3,3]` on each axis, scalar range `[0,27]`; its x=0 section must
have 49 points, 72 triangles, bounds `[0,0,-3,3,-3,3]` and scalar range `[0,18]`.
Evidence includes actual adapter/runner/input hashes, host main-thread identity,
initial/final pipeline and recorded restore/inspect/capture call order proving
no parameters were reapplied between restore and capture. Qualification becomes
true only after successful workflow and teardown; failure evidence is written
even if a cleanup assertion fails.

Host-free tests exercise closed schemas, source capacity, input/output geometry
bounds, lost arrays, empty/wrong output, constructor failure, rollback/recovery,
read-only inventory, missing legends, stable source bindings after proxy
replacement, camera/visibility/palette/legend changes during capture, no-clobber
publication, failed decode cleanup, and real Core HTTP cancellation/timeout of
both new mutations against a controlled protocol host. These do not establish
native ParaView or graphical acceptance.

## Host-free validation of revision 2

On Python 3.12.14 with Core/server 0.20.41 and official MCP SDK 1.30.0,
Ruff 0.16.10 lint and formatting passed. Source and isolated installed-wheel
suites each passed **146 tests**, with **18 native/graphical tests deselected**
using `-m 'not paraview'`. The wheel was installed in a new environment, with
read-only reuse of dependency packages; its tests were copied outside the source
tree and run with Python isolation and pytest source-path injection disabled.
The adapter import was verified in that environment's `site-packages`.
No pvpython, GUI, graphical runner, remote CI, PR or publication was invoked.
These results only qualify host-free behavior at the proposed patch.

## Native finding addressed by revision 3

An operator-run installed-wheel check of prior candidate tree
`71b6fce54b9c338f54f84073e6ec3aa3bcb601ec` measured the expected true section:
49 points, 72 triangles, full bounds `[0,0,-3,3,-3,3]` and scalar range `[0,18]`.
It rendered the first configured view, then inspection failed because
`IndexedLookup` existed as a native property without that Python attribute
accessor. Host, pump and listener cleanup passed. This was a failed graphical
qualification, not proof of the five-state roundtrip.

Revision 3 reads the existing property objects directly and adds fixtures shaped
like raw native proxies, with direct named attributes forbidden. They cover
vector/scalar/enum readback, five-element native scalar-array selection with a
two-element semantic pair, bounds before value reads and empty vector handling.
A fresh native run of revision 3 is still required.

Revision 3 host-free source and isolated installed-wheel suites each passed
**151 tests**, with **18 native/graphical tests deselected**. Ruff lint/format,
shell syntax, clean package inventory and installed adapter byte comparison
passed. The independent reviewer also reproduced the missing-attribute failure
on revision 2 and success on revision 3 with both raw and hidden wrapped
property fixtures. These checks do not qualify the native five-state capture.

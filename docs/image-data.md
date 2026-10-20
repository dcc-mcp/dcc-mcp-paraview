# Bounded ImageData generation (candidate)

`create_image_data` is a typed asynchronous operation in `paraview-pipeline`.
It does not accept Python, programmable sources/filters, callbacks or file-based
recipes. It uses the existing Core job, main-affinity dispatcher and owned
pvpython process. Start once, poll the same job to terminal; never replay on a
timeout. This addition has **not yet passed native or HTTP acceptance**.

## Contract and resource budget

Required fields: `name`, `path`, `dimensions`, `origin`, `spacing`, `fields`.
Optional: `active_scalar`, `significant_digits`, `serialization`. All object
schemas are closed. Paths designate new `.vti` files in an existing
workspace-confined directory. Source names and field names are 1–64 character
ASCII identifiers, beginning with a letter, followed by letters/digits/`_`/`-`.
Field names are distinct. Each field has exactly `name`, `dtype: "float64"` and
`expression`. The default active scalar is the first field.

- Three integer dimensions, each at least 2; product at most 1,000,000 points.
- Three finite origins and strictly positive spacings, with absolute magnitude
  at most 1,000,000. Derived endpoints must remain finite, bounded and strictly
  greater than each origin; spacing cannot be smaller than the endpoint ULP
  (collapsed adjacent coordinates are rejected).
- One to four independent scalar point fields, with 32 MiB maximum aggregate
  Float64 payload. This is an array-payload budget, **not a total process RSS
  guarantee**: native readers, writer buffers and verification copies add memory.
- 512 expression characters, 128 AST nodes and depth 16 per expression.
- At most 64,000,000 postfix evaluation steps over all fields and points.
- Conservative serialization budget: `points * fields * 26 + 4096 <= 64 MiB`.
  Thus not every combination of the individual maxima is accepted. Native
  intermediate output and published output are each checked against 64 MiB.
- Existing 32-source session, 1 MiB IPC and bridge timeout bounds remain in force.

Grammar: single-line ASCII decimal finite constants, `x`, `y`, `z`, parentheses, unary `+`/`-`,
`+`, `-`, `*`, `/`, `**` with a literal integer exponent from -8 through 8, and
`exp(one_expression)`. `^` is not exponentiation. Constants become Float64 before
arithmetic. Attributes, subscripts, strings, booleans, keywords, assignments,
comparisons, imports, hex literals, comprehensions and other calls are rejected.
The AST is inspected as data; no `eval`, `exec`, Python compilation or user
callbacks are used. Every arithmetic intermediate must be finite. Overflow,
division by zero and invalid domains fail without publishing an artifact.
Underflow to finite zero is permitted by Float64 arithmetic.

Origin and spacing are normalized to Float64 before coordinate arithmetic,
and coordinate loads in the evaluator are always Float64. JSON integer versus
floating-point spelling cannot select arbitrary-precision integer arithmetic.
Coordinates are evaluated as `origin + index * spacing`, in x-fastest order.
Fields cannot reference each other. `significant_digits` is an optional integer
1–17: each completed field value is independently converted through that many
decimal significant digits and stored as Float64. Intermediates remain unrounded.
Omission retains the computed Float64 values.

## Native construction and evidence

Only `host_driver.py` imports or calls VTK/ParaView. It constructs `vtkImageData`
and native `vtkDoubleArray` point scalars, writes a native XML binary VTI and
reads it with `vtkXMLImageDataReader`. Native type, dimensions, origin, spacing,
point/cell counts, bounds, field order, active scalar, Float64 type, component and
tuple counts, finite values and every Float64 value are verified. Comparison
uses little-endian IEEE754 bytes, including the sign of zero, not only extrema.
A per-field `sha256_le_float64` is compact evidence of the same full readback.

Default `serialization: "native"` publishes the verified native XML bytes.
`canonical_ascii_v1` serializes only the verified native readback arrays, and is
then independently re-read with the native XML reader and compared again.
Success also creates a named ordinary `XMLImageDataReader` for the final file,
fetches its native output and repeats full comparison. The source is therefore
reader-backed, compatible by design with existing file-backed PVSM logic, not
an ephemeral TrivialProducer. Native save/reopen, contour and section acceptance
remain required before claiming that design works on a qualified host.

Returned provenance includes the bounded expressions, precision, serialization
profile, array/value hashes, budgets and exact host/Core/server/adapter versions.
No large arrays are returned over IPC. Core's existing success/error envelope
and job owner remain authoritative.

## Versioned canonical ASCII profile

This is a generic format, not a dataset preset or stored historical payload:

1. ASCII encoding, LF line endings and final LF. XML declaration is
   `<?xml version="1.0"?>`.
2. VTKFile attributes, in order: `type="ImageData"`, `version="0.1"`,
   `byte_order="LittleEndian"`.
3. ImageData attributes: `WholeExtent`, `Origin`, `Spacing`. Extents are the
   three `0 dimension-1` pairs. Coordinates use shortest Python Float64 `repr`,
   except exactly integral coordinates use integer decimal spelling.
4. Piece has `Extent`; PointData has `Scalars`; fields preserve request order.
5. Each DataArray has, in order: `type="Float64"`, `Name`,
   `NumberOfComponents="1"`, `format="ascii"`. Values are x-fastest, separated
   by one space, on one line, without leading/trailing spaces. Formatting is
   `.Ng` where N is `significant_digits`, or 17 if omitted.
6. Exact element/newline layout is covered by a small unrelated golden fixture
   in `tests/test_image_data.py`. Serialization streams in 1024-value chunks;
   chunk boundaries insert spaces, not additional line breaks.

Numerical equality and artifact-byte identity are separate claims. The full
analytic regression checks every value against an independent mathematical
oracle and separately checks the expected historical hash. Neither fixture's
formula, dimensions, names nor expected hash appears in production code.

## Output ownership, failure and cancellation

Output parents are opened one component at a time with directory descriptors
and `O_NOFOLLOW`. Staging and no-clobber hard-link publication are anchored to
that descriptor, so replacing a parent path with a symlink cannot redirect the
write. Two private stage names include the random existing IPC request ID.
Pre-existing stage names and destinations are rejected. No overwritten-file
mode is exposed. Workspaces remain operator-owned; this is not a multi-user
hostile-filesystem isolation service.

The sidecar pins its own output-parent descriptor before sending the request
and includes that descriptor's device/inode identity in the internal typed IPC
envelope, not in user arguments. The host opens its parent and must match both
values before allocating arrays or creating stages. Replacing the parent with
a different ordinary directory is therefore rejected; after both descriptors
are open, a rename cannot separate their cleanup target.
After the host responds or has been terminated, it cleans only the exact two
request-owned regular-file stage names, never the published destination or
another request's stages. The live host also cleans stages on failure and
removes a failed newly registered reader, restoring the previous active source.

Cancellation/timeouts still terminate the whole owned host process group and
close the session. They do not resume native computation. A file already
published, including a verified file preceding a final reader failure, remains
on disk: **no rollback of published artifacts is promised**. If both the sidecar
and host are killed abruptly, cleanup cannot be guaranteed. Never treat an
interrupted request as permission to overwrite or replay; inspect the existing
job and output in a new managed session.

## Qualification still required

Host-free tests use controlled objects/processes and are not native evidence.
Before publication/qualification, run the exact pinned 0.20.41 sidecar with
ParaView 5.13.2 from an installed wheel outside the checkout. Discover through
real HTTP, load the skill, page through tool discovery, run small and full
fixtures, check the job envelope and full native value hashes, separately check
canonical bytes, then test PVSM save/reopen, contour/slice and cancellation.
Rendering is a separate DISPLAY-backed gate and unnecessary for this capability.

# Bounded hosted coarse-image qualification

This opt-in fixture makes **one new** 41 × 33 × 25 analytic ImageData volume and
one 720 × 480 SectionY image. It does not reproduce the historical coarse image,
replace the original five panels, or rerun the 510,705-point local qualification.
The camera and color controls come from the retained historical SectionY trace
55; only the output path changes.

## Runtime identity

- GitHub-hosted `ubuntu-24.04`, not the pre-existing self-hosted native job.
- Official Kitware ParaView 5.13.2 MPI Linux **Python 3.10** distribution.
- Download-only run
  [39483508573](https://github.com/dcc-mcp/dcc-mcp-paraview/actions/runs/39483508573)
  froze its size and SHA256 in `paraview-5.13.2-hosted-runtime.json`.
- The official legacy MD5 checksum is a consistency check. The first-computed
  SHA256 is a reproducibility binding, not an independent publisher signature.
- Sidecar Python 3.12.14, Core/server 0.20.41 and MCP SDK 1.30.0.
- The candidate wheel's actual hash and current workflow commit are recorded.
  Its 27 runtime package files must match source commit
  `9100157ebc60bc30e8f49fce0ce887058377059e`. Later fixture/CI commits are not
  misrepresented as the commit run in the earlier local qualification.
- This new native lane is **not** the previously qualified distro Python 3.13
  runtime. Actual owned executable and loaded Python library paths are checked.

## Acceptance and limits

The caller uses real initialized HTTP MCP, actual skill/tool discovery and
schemas, existing Core job IDs, and exactly one submission of each mutation.
It verifies all 67,650 scalar Float64 values independently, the slice geometry,
native camera/projection/LUT controls, a fully decoded nonflat PNG, and native
state save/reopen with unchanged presentation and artifact hashes.

One schema-valid over-budget request must return `resource_limit` before sending
a native host request, leaving the valid volume and pipeline unchanged. It must
not allocate a second volume or produce another image.

The caller requires a fresh owned run directory, records bounded raw receipts
and artifact hashes, and closes/waits only its owned native process and service.
The workflow owns its isolated Xvfb display. Failure does not trigger retries.
The fixture's numerical/structural acceptance does not replace visual review.
Generated state files and raw receipts remain qualification artifacts pending
separate privacy, portability and publication review.

No local display/socket restriction is bypassed. No PC8 or existing self-hosted
runner is selected. The existing native job remains disabled unless separately
and explicitly requested.

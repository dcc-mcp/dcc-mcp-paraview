# dcc-mcp-paraview


A typed MCP adapter for a persistent, isolated ParaView pipeline. It creates and edits spheres, clips with planes, reads single-file VTI/VTP/VTU datasets, extracts contours, exports verified datasets, saves native PVSM states, and supports display-backed PNG previews.

**Status:** local Core/server 0.20.41 upgrade candidate; native requalification is pending. Historical Linux acceptance used ParaView 5.13.2, Core/server 0.20.39, Python 3.12.14 and official MCP SDK 1.30.0. Those results do not qualify this upgrade. No published release is claimed. Existing ParaView GUI sessions are untouched.

## Start

Install ParaView from its official distribution or your supported system package source. Keep its `pvpython` interpreter separate from the adapter's Python environment.

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
mkdir -p artifacts
.venv/bin/dcc-mcp-paraview --workspace "$PWD/artifacts" --pvpython /path/to/pvpython
```

The process prints its direct loopback `mcp_url` and registry `instance_id`. The default port is ephemeral; use `--port` or `DCC_MCP_PARAVIEW_PORT` when a fixed direct endpoint is needed. `DCC_MCP_PARAVIEW_EXECUTABLE` also selects `pvpython`. Ctrl+C shuts down the endpoint, registry entry, pump and owned host process group. Startup enforces the candidate Core/server 0.20.41 pins and ParaView 5.13.2 host gate. The Linux-only product gate is unchanged. Native pipeline, real HTTP lifecycle/cancellation and display-backed render acceptance must be repeated on this runtime before qualification. See [the upgrade record](docs/RUNTIME_UPGRADE_0_20_41.md).

The adapter defaults Core's gateway port to zero, so it does not elect a gateway or open a LAN listener. A studio can explicitly configure Core gateway options through the Python server constructor after reviewing deployment policy.

## MCP workflow

1. Initialize the direct MCP endpoint
2. Call `search_skills` with `{"query":"paraview"}`
3. Call `load_skill` with `{"skill_name":"paraview-pipeline"}`
4. Follow all pages of `tools/list`; call the returned tool names
5. For each async result, poll its existing Core `job_id` with `jobs_get_status` to a terminal state

On the historically tested Core 0.20.39, `tools/list` exposes short names such as `create_sphere`; discovery also supplies namespaced slugs. Do not guess or replay a mutation after a timeout.

| Tool | Purpose |
|---|---|
| `get_adapter_info` | Pure host-independent limits and formats |
| `inspect_pipeline` | Source geometry, point arrays and exact host-thread identity |
| `create_sphere`, `edit_sphere` | Bounded sphere parameters, verified after writes |
| `clip_plane` | Native plane clip of an existing input |
| `open_dataset` | Local single-file VTI, VTP or VTU import |
| `contour` | At most eight isosurfaces of a named point scalar |
| `export_dataset` | VTI/VTP/VTU export with native geometry/array readback |
| `save_state`, `reopen_state` | Native PVSM save and trusted same-session reopen |
| `render_preview` | PNG, scalar palette/range, camera and background |

See [the installable skill](src/dcc_mcp_paraview/skills/paraview-pipeline/SKILL.md) and [exact tool schemas](src/dcc_mcp_paraview/skills/paraview-pipeline/tools.yaml).

## Deliberate limits

- The candidate is Linux-only with Python 3.10–3.12 for the sidecar, independently of vendor Python. Historical native qualification used Core/server 0.20.39; repository CI targets 3.10/3.12 but has not run for this upgrade. Windows pipe handling is not implemented; Python 3.7 LTS acceptance is not claimed
- No attachment to existing GUI pipelines, arbitrary Python, programmable filters, remote servers, distributed MPI, time-series readers or general PVSM imports
- Native state can contain executable filters. `reopen_state` accepts only unchanged files saved by the same live adapter session. Saved PVSM remains a native editable artifact for ParaView; after an adapter restart use verified datasets or open the native state yourself in ParaView
- All file operations are confined to one operator-owned workspace. Inputs are limited to 64 MiB and pipelines to 32 sources. Outputs never overwrite; all symlink path components are rejected. This is a single-user tool boundary, not an OS sandbox for hostile concurrent filesystem changes or hostile native file parser inputs
- Rendering requires a working `DISPLAY`; use the opt-in isolated-display acceptance test. Native output dimensions, decoded pixels, camera, background and scalar transfer function are verified. EGL/OSMesa auto-detection is intentionally absent. A defined but broken display can still crash the owned host
- Native calls are monolithic. Timeout/cancellation terminates the owned host, discards its unsaved state and permanently closes that session. A file operation already published before interruption may remain. No rollback or cross-restart job recovery is claimed

[Architecture](docs/architecture.md) · [Installation and removal](install.md) · [Validation evidence](docs/validation.md)

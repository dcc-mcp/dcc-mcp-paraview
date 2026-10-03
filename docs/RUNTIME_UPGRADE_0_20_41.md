# Core/server 0.20.41 upgrade candidate

This local candidate pins the official `dcc-mcp-core` and `dcc-mcp-server`
0.20.41 distributions. The operator CLI is independently installed and checked
as official `dcc-mcp-cli` 0.20.41 in the isolated upgrade environment. CLI is not
an adapter dependency. The adapter version remains 0.1.0; this is not a release.

## Frozen baseline and rollback

The previous source is Git commit
`080662fc30146bf2be7650412889570ac3a925c8` on
`review/windows-test-scope-paraview-20261002`. Its Core/server pins are 0.20.39.
The new worktree/branch `review/runtime-0.20.41-paraview-20261003` starts at that
exact commit. The previous worktree, isolated installed environment, wheels and
complete hardening bundle remain unchanged. The upgrade changes neither the MIT
license nor ParaView installation/user sessions.

Install the candidate wheels into a new Python environment and use a fresh
operator-owned workspace. Export wanted data before stopping an owned adapter.
Switch the configured MCP endpoint only after acceptance. If acceptance fails,
stop the candidate, use the preserved 0.20.39 environment/source and restore the
previous endpoint configuration. Do not install into vendor Python, overwrite
outputs, downgrade an existing environment in place or change global settings.
Manual rollback does not restore unsaved state discarded by an interrupted host.

## Candidate changes and scope

Dependency pins, the exact pre-admission runtime gate, bootstrap minimum and the
skill profile move together to 0.20.41. Both previous Core and previous server
0.20.39 versions are rejected before creating a host session or Core transport.
`get_adapter_info` retains `qualified_core_version` for compatibility, but reports
`runtime_status=upgrade_candidate`, `native_qualified=false` and
`historical_native_core_version=0.20.39` so that field alone is not a qualification.

The product still requires Linux and ParaView 5.13.2. Windows checks verify
portable contracts, import safety, dispatcher cancellation and the platform/version
gates. The existing thirty Linux/POSIX fixture exclusions, missing native host
exclusions and opt-in display exclusion retain their original narrow scope. They
do not establish Windows host support or live Linux HTTP/native acceptance.
A default loopback binding is not proof that embedded transport fallback,
authentication or stock administrative actions are safe for deployment. This
candidate does not claim those risks are resolved.

## Fresh validation and native handoff

Fresh Windows checks use Python 3.12 and official MCP SDK 1.30.0 with the actual
Core/server/CLI 0.20.41 distributions. Lint, format, pytest and wheel/sdist builds
are recorded outside the source tree, including the actual resolved versions,
individual skip reasons and any first-attempt failures. Root integration also
checks the installed wheel import origin and declared plugin/CLI entry points.
Final exact counts and wheel hashes belong to the new runtime-upgrade receipts,
not the historical source evidence.

All existing `docs/validation.md`, `docs/render-styling-validation.md`, JSON
acceptance receipts, installed-wheel origin and pinned validation requirements
remain unchanged 0.20.39 historical artifacts. They do not qualify 0.20.41.
No remote CI, draft PR, merge, release or deployment was executed for this upgrade.

On the exact new candidate, the Linux author must rerun the real Core HTTP MCP
transport, skill discovery/pagination, async schemas/jobs/REST cancellation,
owned-host failure cleanup, startup/shutdown and restart with a fresh server
instance, native pipeline/data/PVSM readback and opt-in display-backed render
acceptance. Record exact Core/server/SDK/Python/ParaView versions and installed
wheel origins. Keep resource/no-overwrite/external-state guards and host thread
ownership checks enabled; missing native/display results remain blockers.

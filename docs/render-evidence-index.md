# Render evidence provenance

The committed JSON files below are immutable historical records from the
Core/server 0.20.39 hardening revision 1. Their producing
`tests/test_render_mcp.py` revision is identified by its LF-text SHA256:
`944deb460236ea1661b5ab27eee8c7b5f2672eef40babedbea46ff3e5a062ef6`.

| Historical record | SHA256 |
|---|---|
| [Source render evidence](render-source-evidence.json) | `0b49908b10e4e5920a7f3bbdcf877d128a2d1f9117a647e4a4e0ffc6535bdc41` |
| [Installed render evidence](render-installed-evidence.json) | `1f432a8bbe79869094efa90244955ef07f4d96ff2f9cb5b2802ff9752e86eae0` |

Those records contain six operations and the earlier `control_point_count` and
`control_points_sha256` readback fields. They do not demonstrate the later
camera/material/legend and portable-state relocation cases. The later test
emits `color_control_points` and additional solid, legend, hidden-again,
portable-save, relocated-reopen and relocated-render operations. Its LF-text
SHA256 at commit `dbde182743f804a8c005d2432e3ab1e8ba97eda9` is
`d03bf57820690fe40aa90c9ceb2a30affe375f35ff07086f0b1c2091b68f5712`.

Separate delegated Linux source and installed-wheel acceptance passed those
later cases with Core/server 0.20.41 at that exact commit; see
[validation](validation.md). No new native run or replacement JSON is claimed
for subsequent review fixes. The current revision needs its own native
acceptance before inheriting that qualification.

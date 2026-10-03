# Native portable PVSM acceptance

The native reader-property route has been verified on ParaView5.13.2. SaveState
has no relative-path option in that version; setting its supported reader
FileName properties while the owned host works in the package directory produces
relative FileName and FileNameInfo values without serialized-XML edits.

`save_state(..., data_directory=...)` is opt-in. Only byte-identical copies of
adapter-created single-file VTI/VTU/VTP readers are admitted, in a directory inside
the output state's parent. The live original reader filenames and working directory
are restored. The unchanged package data must already exist; the operation never
adopts, downloads, rewrites or overwrites data files. Existing no-clobber output
publication remains in force.

Native SaveState output is inspected before publication. Reader path properties
must be exactly the intended relative names. Absolute filesystem references and
workspace strings are rejected. ParaView's literal '/' BlockSelectors value is
recognized as a data-assembly selector, not a filesystem path.

`reopen_state(..., data_directory=...)` requires current-session state-hash trust,
checks every relocated data digest, then calls native LoadState with exact reader
IDs and filenames. It never searches other directories or falls back to old paths.
A new session does not trust a caller-supplied manifest or arbitrary PVSM.

Acceptance includes native data readback, closed schemas, unknown/wrong/changed
inputs, missing data, symlinks, traversal, wrong package ancestry, and byte-preserved
state relocation. The test renames the original source and package away so success
cannot depend on their old paths. Through official MCP, the relocated rendering
is byte-identical to the pre-move PNG; reader path and full pipeline readbacks match.
A repeated native SaveState/reopen/view workflow also continues to pass.

Only the established Linux host/interpreter/Core profile is qualified. All package
copies are synthetic. This extends project portability, not OS compatibility,
host-complete installation, protocol certification, or general production readiness.

## Measured result

The final source and independent fresh installed-wheel suites each pass100 tests
with the one explicitly opt-in display test skipped in the shell. That display
test passes separately against both source and the installed wheel, including
native portable SaveState, relocated exact reader mappings, and identical PNG
SHA256 `928e0a09ccaa7874ce1fd0a04a20d67b7e5f59d307f69cfe10db21ef471d5a9f`.
These repeated runs are not additive unique test counts. The final wheel is
`81965de11ae7541dfa9dc04a9badb8dec0dcfa018af998bc0441a51818a3fe70`.

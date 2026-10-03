# ParaView adapter engineering

- Keep this adapter a Core composition root. Use DccServerBase, DccServerOptions and HostExecutionBridge public APIs.
- ParaView imports and API calls belong only to host_driver.py, running on the owned pvpython main thread.
- Reuse HostUiDispatcherBase queues and job/cancellation owners. Do not add raw Python execution tools.
- Every mutation has a closed typed schema, native readback, and canonical skill result envelope.
- File paths stay inside the operator-selected workspace. Preserve no-overwrite publication and rejection of external PVSM.
- Never modify an already-open GUI session during tests. All smoke artifacts are synthetic and isolated.
- Run `.venv/bin/ruff check src tests`, `.venv/bin/ruff format --check src tests`, and `.venv/bin/pytest`.
- `pytest -m 'not paraview'` is host-free CI. Never describe it as live acceptance.
- Record exact Core, SDK and host versions. Keep render limitations and Python/OS support honest.
- Releases, PyPI upload, credentials and repository publication are separate authorized work; CI here never releases.

"""Host-free checks for the download-qualified extraction fixture."""

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def extraction(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts/prepare-qualified-runtime.py"
    if not script.exists():
        pytest.skip("Repository-only workflow helper is not shipped in the runtime wheel")
    spec = importlib.util.spec_from_file_location("qualified_extract", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(tarfile, "data_filter"):
        pytest.skip("The production hosted fixture requires Python 3.12.14")
    root = tmp_path / "fixture"
    (root / "scripts").mkdir(parents=True)
    (root / "docs/qualification").mkdir(parents=True)
    monkeypatch.setattr(module, "__file__", str(root / "scripts/prepare-qualified-runtime.py"))
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: SimpleNamespace(free=8 * 1024**3))

    def invoke(extra=None, wrong_hash=False):
        archive = root / "Vendor.tar.gz"
        with tarfile.open(archive, "w:gz") as target:
            executable = tarfile.TarInfo("Vendor/bin/pvpython")
            executable.mode = 0o755
            executable.size = 16
            target.addfile(executable, io.BytesIO(b"not executed!!!!"))
            if extra:
                target.addfile(extra, io.BytesIO(b"x" * extra.size))
        lock = {
            "bytes": archive.stat().st_size,
            "sha256": "0" * 64 if wrong_hash else hashlib.sha256(archive.read_bytes()).hexdigest(),
            "provenance_run_url": "https://example.test/fixture-only",
        }
        (root / "docs/qualification/paraview-5.13.2-hosted-runtime.json").write_text(json.dumps(lock))
        destination = root / "extracted"
        monkeypatch.setattr("sys.argv", ["prepare-qualified-runtime.py", str(archive), str(destination)])
        module.main()
        return destination

    return invoke


def test_exact_archive_extracts_without_execution(extraction):
    destination = extraction()
    report = json.loads((destination / "extraction.json").read_text())
    assert report["native_executed"] is False
    assert (destination / "Vendor/bin/pvpython").read_bytes() == b"not executed!!!!"


def test_wrong_hash_is_rejected(extraction):
    with pytest.raises(RuntimeError, match="SHA256"):
        extraction(wrong_hash=True)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "Other/file", "Vendor/bin/pvpython"])
def test_unsafe_or_duplicate_members_rejected(extraction, name):
    member = tarfile.TarInfo(name)
    with pytest.raises(RuntimeError, match="member"):
        extraction(member)


def test_external_symlink_rejected(extraction):
    member = tarfile.TarInfo("Vendor/escape")
    member.type = tarfile.SYMTYPE
    member.linkname = "../../outside"
    with pytest.raises(tarfile.FilterError):
        extraction(member)


def test_special_file_rejected(extraction):
    member = tarfile.TarInfo("Vendor/device")
    member.type = tarfile.CHRTYPE
    with pytest.raises(tarfile.FilterError):
        extraction(member)

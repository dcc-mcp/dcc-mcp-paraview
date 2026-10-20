"""Repository-only checks; none launches ParaView or an HTTP service."""

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


def load_fixture():
    script = Path(__file__).resolve().parents[1] / "scripts/hosted_coarse_candidate.py"
    if not script.exists():
        pytest.skip("Workflow fixture is not part of the installed runtime wheel")
    spec = importlib.util.spec_from_file_location("coarse_fixture", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return script, module


def test_isolated_help_loads_fixed_companions():
    script, _ = load_fixture()
    result = subprocess.run([sys.executable, "-I", str(script), "--help"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "--runtime-archive" in result.stdout


def test_companions_match_frozen_package_sources():
    script, module = load_fixture()
    helper, parameters = module.load_fixture_support()
    source = script.parents[1] / "src/dcc_mcp_paraview"
    actual = {
        p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }
    assert actual == helper.PACKAGE_MANIFEST
    assert parameters["create"]["dimensions"] == [41, 33, 25]
    assert parameters["render"]["width"] == 720
    assert parameters["render"]["height"] == 480
    assert parameters["render"]["camera_parallel_scale"] == 1.8


def test_local_execution_rejected_before_output(tmp_path, monkeypatch):
    _, module = load_fixture()
    destination = tmp_path / "must-not-exist"
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fixture",
            "--run-dir",
            str(destination),
            "--wheel",
            str(tmp_path / "unused.whl"),
            "--wheel-sha256",
            "0" * 64,
            "--paraview-root",
            str(tmp_path / "unused-runtime"),
            "--runtime-archive",
            str(tmp_path / "unused.tar.gz"),
            "--runtime-provenance",
            str(tmp_path / "unused.json"),
            "--hosted-window-confirmed",
        ],
    )
    with pytest.raises(RuntimeError, match="GitHub-hosted"):
        module.main()
    assert not destination.exists()

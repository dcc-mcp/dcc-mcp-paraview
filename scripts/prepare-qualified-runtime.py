#!/usr/bin/env python3
"""Extract only the exact download-qualified official runtime; never run it."""

import argparse
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path, PurePosixPath


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    lock = json.loads(
        (Path(__file__).resolve().parents[1] / "docs/qualification/paraview-5.13.2-hosted-runtime.json").read_text()
    )
    if args.destination.exists():
        raise RuntimeError("Extraction destination must be fresh")
    if args.archive.stat().st_size != lock["bytes"]:
        raise RuntimeError("Archive size differs from the completed download qualification")
    with args.archive.open("rb") as source:
        hasher = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
        digest = hasher.hexdigest()
    if digest != lock["sha256"]:
        raise RuntimeError("Archive SHA256 differs from the completed download qualification")
    if shutil.disk_usage(args.destination.parent).free < 6 * 1024**3:
        raise RuntimeError("Need 6 GiB free for this hosted extraction and bounded single-image fixture")
    expected_root = args.archive.name.removesuffix(".tar.gz")
    with tarfile.open(args.archive, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) > 100000 or sum(m.size for m in members) > 4 * 1024**3:
            raise RuntimeError("Archive exceeds the reviewed extraction budget")
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != expected_root:
                raise RuntimeError("Unexpected archive member path")
            if member.name in names:
                raise RuntimeError("Duplicate archive member")
            names.add(member.name)
            # data_filter also rejects external link targets, devices and special files.
            tarfile.data_filter(member, str(args.destination))
        args.destination.mkdir()
        archive.extractall(args.destination, members=members, filter="data")
    executable = args.destination / expected_root / "bin/pvpython"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError("Official archive did not contain its expected executable")
    if not executable.resolve().is_relative_to(args.destination.resolve()):
        raise RuntimeError("Executable escapes the extracted runtime")
    report = {
        "archive_sha256": digest,
        "archive_bytes": lock["bytes"],
        "member_count": len(members),
        "uncompressed_member_bytes": sum(m.size for m in members),
        "pvpython": str(executable),
        "pvpython_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "native_executed": False,
        "download_qualification": lock["provenance_run_url"],
    }
    (args.destination / "extraction.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()

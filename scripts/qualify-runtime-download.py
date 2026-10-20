#!/usr/bin/env python3
"""Download-only provenance preflight. Never extract or execute the native archive."""

import argparse
import hashlib
import json
import os
import shutil
import time
import urllib.request
from pathlib import Path

BASE = "https://www.paraview.org/files/v5.13/"
NAME = "ParaView-5.13.2-MPI-Linux-Python3.10-x86_64.tar.gz"
PUBLISHED_MD5 = "65af356984e1a9054007f8398a671af2"
MAX_BYTES = 800 * 1024 * 1024


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    directory = args.output_directory
    if directory.exists():
        raise RuntimeError("Fresh download directory required; never overwrite or retry automatically")
    if shutil.disk_usage(directory.parent).free < 2 * 1024**3:
        raise RuntimeError("Need at least 2 GiB free before this isolated hosted download")
    directory.mkdir()
    started = time.monotonic()
    with urllib.request.urlopen(BASE + "md5sum.txt", timeout=30) as response:
        if not response.url.startswith("https://www.paraview.org/"):
            raise RuntimeError("Unexpected checksum-list redirect")
        published = response.read(32769)
    if len(published) > 32768:
        raise RuntimeError("Checksum list exceeds the expected bounded metadata size")
    (directory / "official-md5sum.txt").write_bytes(published)
    matches = [
        line.split()[0]
        for line in published.decode("utf-8").splitlines()
        if len(line.split()) == 2 and line.split()[1] == "v5.13/" + NAME
    ]
    if matches != [PUBLISHED_MD5]:
        raise RuntimeError("Official checksum list does not match the reviewed release identity")
    digest, legacy_digest, count = hashlib.sha256(), hashlib.md5(), 0
    with urllib.request.urlopen(BASE + NAME, timeout=60) as response:
        final_url = response.url
        if not final_url.startswith("https://www.paraview.org/"):
            raise RuntimeError("Unexpected archive redirect")
        with (directory / NAME).open("xb") as output:
            while True:
                if time.monotonic() - started > 600:
                    raise TimeoutError("Download exceeded its total wall budget; no retry")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > MAX_BYTES:
                    raise RuntimeError("Archive exceeds its bounded download size")
                output.write(chunk)
                digest.update(chunk)
                legacy_digest.update(chunk)
    if legacy_digest.hexdigest() != PUBLISHED_MD5:
        raise RuntimeError("Downloaded archive differs from the official release checksum")
    result = {
        "status": "download_verified_not_executed",
        "official_url": BASE + NAME,
        "final_url": final_url,
        "official_checksum_url": BASE + "md5sum.txt",
        "official_checksum_file_sha256": hashlib.sha256(published).hexdigest(),
        "bytes": count,
        "sha256": digest.hexdigest(),
        "md5": legacy_digest.hexdigest(),
        "archive_extracted": False,
        "native_executed": False,
        "native_release": "ParaView 5.13.2 MPI Linux x86_64 Python3.10",
        "not_claimed": (
            "This is not the locally qualified distro Python3.13 build. MD5 is a legacy consistency check; "
            "first-computed SHA256 is a reproducibility binding, not an independent publisher signature."
        ),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_commit": os.environ.get("GITHUB_SHA"),
        "wall_seconds": time.monotonic() - started,
    }
    (directory / "download-provenance.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()

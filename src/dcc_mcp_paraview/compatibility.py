"""Pinned upgrade candidate, checked before admitting host work."""

import sys
from importlib.metadata import PackageNotFoundError, version

from .contracts import OperationError

CORE_VERSION = "0.20.41"
HOST_VERSIONS = ("5.13.2",)


def check_core_runtime():
    if sys.platform != "linux":
        raise OperationError("unsupported_platform", "This measured adapter profile requires Linux")
    for distribution in ("dcc-mcp-core", "dcc-mcp-server"):
        try:
            actual = version(distribution)
        except PackageNotFoundError:
            actual = "missing"
        if actual != CORE_VERSION:
            raise OperationError(
                "unsupported_runtime",
                "%s %s is not supported; install the pinned %s candidate" % (distribution, actual, CORE_VERSION),
            )

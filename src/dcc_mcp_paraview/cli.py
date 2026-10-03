"""Guard startup imports so Core records bootstrap failures."""

from . import __version__


def main():
    from dcc_mcp_core.host_errors import capture_bootstrap_errors

    with capture_bootstrap_errors("paraview", adapter_version=__version__, min_core_version="0.20.41"):
        from .server import main as serve

        serve()

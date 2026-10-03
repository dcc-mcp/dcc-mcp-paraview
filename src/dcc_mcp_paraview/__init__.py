"""ParaView's host modules are imported only in the owned pvpython process."""

__version__ = "0.1.0"


def __getattr__(name):
    if name == "ParaViewMcpServer":
        from .server import ParaViewMcpServer

        return ParaViewMcpServer
    raise AttributeError(name)

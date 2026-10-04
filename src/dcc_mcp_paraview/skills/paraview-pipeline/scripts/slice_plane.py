from dcc_mcp_core.skills_helper import run_main

from dcc_mcp_paraview.bridge import call


def main(**kwargs):
    return call("slice_plane", **kwargs)


if __name__ == "__main__":
    run_main(main)

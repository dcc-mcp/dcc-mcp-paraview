from dcc_mcp_core.skills_helper import run_main, skill_success


def main():
    return skill_success(
        "ParaView adapter capabilities",
        adapter_version="0.1.0",
        qualified_core_version="0.20.41",
        runtime_status="upgrade_candidate",
        native_qualified=False,
        historical_native_core_version="0.20.39",
        qualified_host_versions=["5.13.2"],
        platform="linux",
        dataset_formats=["vti", "vtp", "vtu"],
        max_sources=32,
        max_file_bytes=67108864,
        host_imported=False,
    )


if __name__ == "__main__":
    run_main(main)

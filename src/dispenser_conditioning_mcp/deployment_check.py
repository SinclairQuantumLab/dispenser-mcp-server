"""Offline operator check for one protected MCP host installation."""

from __future__ import annotations

import sys

from dispenser_conditioning_mcp.config import HiCubeConfiguration, SiglentConfiguration
from dispenser_conditioning_mcp.hicube import (
    HiCubeNeoPressureSource,
    validate_hicube_client_installation,
)
from dispenser_conditioning_mcp.server import create_server
from dispenser_conditioning_mcp.siglent import (
    DispenserPowerController,
    validate_siglent_driver_installation,
)


def main() -> None:
    """Validate local configuration and imports without contacting a device."""

    try:
        hicube = HiCubeConfiguration.from_environment()
        siglent = SiglentConfiguration.from_environment()
        validate_hicube_client_installation(hicube.client_file)
        validate_siglent_driver_installation(siglent.driver_src)
        with siglent.gateway_auth_file.open("rb") as auth_file:
            auth_file.read(0)
        source = HiCubeNeoPressureSource(
            client_file=hicube.client_file,
            host=hicube.host,
            port=hicube.port,
            timeout_s=hicube.timeout_s,
        )
        create_server(source, DispenserPowerController(siglent))
    except Exception as error:
        print(
            "Offline deployment validation failed. Ask the operator to review "
            "protected local diagnostics.",
            file=sys.stderr,
        )
        raise SystemExit(2) from error
    print("Offline deployment validation passed.")


if __name__ == "__main__":
    main()

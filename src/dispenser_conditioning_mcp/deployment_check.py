"""Offline operator check for one protected MCP host installation."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import NoReturn

from dispenser_conditioning_mcp.config import ConfigurationError, OperatorConfiguration
from dispenser_conditioning_mcp.hicube import (
    HiCubeNeoPressureSource,
    validate_hicube_client_installation,
)
from dispenser_conditioning_mcp.server import create_server
from dispenser_conditioning_mcp.siglent import (
    DispenserPowerController,
    validate_siglent_driver_installation,
)
from dispenser_conditioning_mcp.transport import McpTransportConfiguration


class _SafeArgumentParser(argparse.ArgumentParser):
    """Reject invalid CLI input without echoing its potentially sensitive text."""

    def error(self, message: str) -> NoReturn:
        del message
        self.exit(2, f"{self.prog}: error: unsupported command-line option.\n")


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _SafeArgumentParser(
        prog="python -m dispenser_conditioning_mcp.deployment_check",
        description="Validate local MCP configuration without contacting a device.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="append only the exception class to sanitized stage failures",
    )
    return parser.parse_args(argv)


def _fail(
    stage: str,
    message: str,
    *,
    error: BaseException,
    diagnostic: bool,
) -> NoReturn:
    exception_detail = ""
    if diagnostic:
        exception_name = type(error).__name__
        if not exception_name.isidentifier() or len(exception_name) > 128:
            exception_name = "Exception"
        exception_detail = f" exception={exception_name}"
    print(
        f"Offline deployment validation failed [{stage}]: {message}{exception_detail}",
        file=sys.stderr,
    )
    raise SystemExit(2) from None


def _validate_auth_access(operator: OperatorConfiguration) -> None:
    """Verify open permission without reading authentication contents."""

    with operator.siglent.gateway_auth_file.open("rb"):
        pass


def main(argv: Sequence[str] | None = None) -> None:
    """Validate local configuration and imports without contacting a device."""

    arguments = _arguments(argv)
    diagnostic = bool(arguments.diagnostic)

    try:
        operator = OperatorConfiguration.from_toml()
    except ConfigurationError as error:
        _fail("CONFIG", str(error), error=error, diagnostic=diagnostic)
    except Exception as error:
        _fail(
            "CONFIG",
            "Operator settings could not be loaded safely.",
            error=error,
            diagnostic=diagnostic,
        )

    try:
        McpTransportConfiguration.from_settings(operator.startup)
    except ConfigurationError as error:
        _fail("TRANSPORT_POLICY", str(error), error=error, diagnostic=diagnostic)
    except Exception as error:
        _fail(
            "TRANSPORT_POLICY",
            "Startup transport policy could not be validated.",
            error=error,
            diagnostic=diagnostic,
        )

    hicube = operator.hicube
    siglent = operator.siglent
    try:
        validate_hicube_client_installation(hicube.client_file)
    except Exception as error:
        _fail(
            "HICUBE_IMPORT",
            "The vendored HiCube client or an installed dependency could not be "
            "imported.",
            error=error,
            diagnostic=diagnostic,
        )

    try:
        validate_siglent_driver_installation(siglent.driver_src)
    except Exception as error:
        _fail(
            "SIGLENT_IMPORT",
            "The pinned Siglent source, import origin, or required public API is "
            "invalid.",
            error=error,
            diagnostic=diagnostic,
        )

    try:
        _validate_auth_access(operator)
    except Exception as error:
        _fail(
            "AUTH_ACCESS",
            "The gateway authentication file is not readable by this process identity.",
            error=error,
            diagnostic=diagnostic,
        )

    try:
        source = HiCubeNeoPressureSource(
            client_file=hicube.client_file,
            host=hicube.host,
            port=hicube.port,
            timeout_s=hicube.timeout_s,
        )
        create_server(source, DispenserPowerController(siglent))
    except Exception as error:
        _fail(
            "SERVER_ASSEMBLY",
            "Offline MCP and controller assembly failed.",
            error=error,
            diagnostic=diagnostic,
        )
    print("Offline deployment validation passed.")


if __name__ == "__main__":
    main()

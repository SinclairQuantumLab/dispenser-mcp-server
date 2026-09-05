"""Preview an existing recorded session without configuring or contacting hardware."""

import argparse
from pathlib import Path

import uvicorn
from starlette.applications import Starlette

from dispenser_conditioning_mcp.dashboard import dashboard_routes
from dispenser_conditioning_mcp.dashboard_access import DashboardAccess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument(
        "--observer-file",
        type=Path,
        help="Optional human-only simulation observer file",
    )
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    access = DashboardAccess()
    app = Starlette(
        routes=dashboard_routes(
            args.session_dir.resolve(),
            observer_file=args.observer_file,
            replay=True,
            access=access,
        )
    )
    access.announce()
    uvicorn.run(
        app, host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False
    )


if __name__ == "__main__":
    main()

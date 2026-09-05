import re
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from test_server_recording import setup

from dispenser_conditioning_mcp.dashboard import dashboard_routes
from dispenser_conditioning_mcp.dashboard_access import DashboardAccess
from dispenser_conditioning_mcp.transport import (
    McpTransportConfiguration,
    create_http_app,
)


@pytest.mark.anyio
async def test_remote_login_all_routes_restart_and_peer_boundary(tmp_path: Path):
    access = DashboardAccess()
    assert re.fullmatch(r"[a-z]+-[a-z]+-\d{2}", access.token)
    app = Starlette(routes=dashboard_routes(tmp_path, access=access))
    remote = httpx.ASGITransport(app=app, client=("192.0.2.50", 4321))
    async with httpx.AsyncClient(
        transport=remote, base_url="http://localhost"
    ) as client:
        for path in (
            "/api/session",
            "/api/runs",
            "/api/simulation-state",
            "/session.js",
            "/vendor/plotly-basic-4.0.0.min.js",
        ):
            response = await client.get(
                path,
                headers={"X-Forwarded-For": "127.0.0.1", "Forwarded": "for=127.0.0.1"},
            )
            assert response.status_code == (
                401 if path == "/api/simulation-state" else 200
            )
            assert access.token not in response.text
        anonymous_page = await client.get("/dashboard")
        assert anonymous_page.status_code == 200
        assert 'data-operator-authorized="false"' in anonymous_page.text
        assert 'id="dashboard-access-phrase"' not in anonymous_page.text
        assert (
            await client.post(
                "/api/runs/rename", json={"run": "", "display_name": "denied"}
            )
        ).status_code == 401
        assert access.token not in anonymous_page.text
        assert (await client.get("/dashboard/operator")).status_code == 403
        assert access.token not in (await client.get("/dashboard/login")).text
        assert (
            await client.post("/dashboard/login", data={"code": "wrong"})
        ).status_code == 401
        logged_in = await client.post(
            "/dashboard/login?run=saved-fixture&archived=true",
            data={"code": "  " + access.token.upper() + "  "},
        )
        assert logged_in.status_code == 303
        assert (
            logged_in.headers["location"]
            == "/dashboard?run=saved-fixture&archived=true"
        )
        assert "HttpOnly" in logged_in.headers["set-cookie"]
        assert "SameSite=strict" in logged_in.headers["set-cookie"]
        assert access.token not in logged_in.headers["set-cookie"]
        for path in (
            "/dashboard",
            "/api/session",
            "/api/runs",
            "/api/simulation-state",
            "/session.js",
        ):
            assert (await client.get(path)).status_code == 200
        authorized_page = await client.get("/dashboard")
        assert access.token in authorized_page.text
        assert "Dashboard access phrase" in authorized_page.text
        assert authorized_page.headers["cache-control"] == "no-store"
        assert (await client.get("/dashboard/operator")).status_code == 403
        cookies = client.cookies
    restarted = Starlette(routes=dashboard_routes(tmp_path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted, client=("192.0.2.50", 4321)),
        base_url="http://localhost",
        cookies=cookies,
    ) as client:
        assert (await client.get("/api/session")).status_code == 200
        assert (await client.get("/api/simulation-state")).status_code == 401
        assert (await client.get("/dashboard")).status_code == 200
        assert access.token not in (await client.get("/dashboard")).text
        assert (
            await client.post("/dashboard/login", data={"code": access.token})
        ).status_code == 401
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 4321)),
        base_url="http://untrusted-host",
    ) as client:
        assert (await client.get("/api/session")).status_code == 200
        assert access.token in (await client.get("/dashboard/operator")).text
        assert access.token in (await client.get("/dashboard")).text


@pytest.mark.anyio
async def test_dashboard_login_does_not_gate_remote_mcp(tmp_path: Path):
    server, _, _ = setup(tmp_path)
    app = create_http_app(server, McpTransportConfiguration(allow_remote_access=True))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("192.0.2.50", 4321)),
            base_url="http://test-server",
        ) as client,
    ):
        assert (await client.get("/api/session")).status_code == 200
        assert (await client.get("/api/simulation-state")).status_code == 401
        result = await client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "auth-boundary-fixture", "version": "1"},
                },
            },
        )
        assert result.status_code == 200
        assert "serverInfo" in result.text


@pytest.mark.anyio
async def test_failed_phrase_throttle_expires_without_waiting(
    tmp_path: Path, monkeypatch
):
    now = [100.0]
    monkeypatch.setattr(
        "dispenser_conditioning_mcp.dashboard_access.time.monotonic", lambda: now[0]
    )
    access = DashboardAccess()
    app = Starlette(routes=dashboard_routes(tmp_path, access=access))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.0.2.50", 4321)),
        base_url="http://test-server",
    ) as client:
        for _ in range(5):
            assert (
                await client.post("/dashboard/login", data={"code": "wrong"})
            ).status_code == 401
        blocked = await client.post("/dashboard/login", data={"code": access.token})
        assert blocked.status_code == 429
        assert blocked.headers["retry-after"] == "60"
        now[0] += 61
        assert (
            await client.post("/dashboard/login", data={"code": access.token})
        ).status_code == 303

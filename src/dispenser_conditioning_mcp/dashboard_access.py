"""Small operator access boundary for dashboard routes, never MCP authorization."""

from __future__ import annotations

import hmac
import ipaddress
import math
import secrets
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable
from html import escape
from urllib.parse import parse_qs

from coolname import generate_slug, replace_random
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

COOKIE_NAME = "dispenser_dashboard"
Endpoint = Callable[[Request], Awaitable[Response]]


class DashboardAccess:
    def __init__(self) -> None:
        replace_random(secrets.SystemRandom())
        self.token = f"{generate_slug(2)}-{secrets.randbelow(100):02d}"
        self.cookie = secrets.token_urlsafe(32)
        self.failed_logins: deque[float] = deque(maxlen=5)

    def announce(self) -> None:
        print(
            f"Operator dashboard access phrase (valid until restart): {self.token}",
            file=sys.stderr,
        )

    @staticmethod
    def local(request: Request) -> bool:
        try:
            return (
                request.client is not None
                and ipaddress.ip_address(request.client.host).is_loopback
            )
        except ValueError:
            return False

    def authorized(self, request: Request) -> bool:
        return self.local(request) or hmac.compare_digest(
            request.cookies.get(COOKIE_NAME, "").encode(), self.cookie.encode()
        )

    def protect(self, endpoint: Endpoint) -> Endpoint:
        async def guarded(request: Request) -> Response:
            if not self.authorized(request):
                if request.url.path in {"/", "/dashboard"}:
                    return RedirectResponse("/dashboard/login", status_code=303)
                return JSONResponse(
                    {"error": "Operator dashboard login required"},
                    status_code=401,
                    headers={"Cache-Control": "no-store"},
                )
            response = await endpoint(request)
            response.headers["Cache-Control"] = "no-store"
            return response

        return guarded

    async def login(self, request: Request) -> Response:
        if request.method == "POST":
            now = time.monotonic()
            while self.failed_logins and now - self.failed_logins[0] >= 60:
                self.failed_logins.popleft()
            if len(self.failed_logins) >= 5:
                return Response(
                    "Too many incorrect phrases; try again shortly.",
                    status_code=429,
                    headers={
                        "Retry-After": str(
                            max(1, math.ceil(60 - (now - self.failed_logins[0])))
                        ),
                        "Cache-Control": "no-store",
                    },
                )
            body = bytearray()
            async for part in request.stream():
                body.extend(part)
                if len(body) > 4096:
                    return Response("Login request too large", status_code=413)
            supplied = (
                parse_qs(body.decode("utf-8", errors="replace"))
                .get("code", [""])[0]
                .strip()
                .lower()
            )
            if not hmac.compare_digest(supplied.encode(), self.token.encode()):
                self.failed_logins.append(time.monotonic())
                return HTMLResponse(
                    self.login_page("Invalid access phrase."),
                    status_code=401,
                    headers={"Cache-Control": "no-store"},
                )
            response = RedirectResponse("/dashboard", status_code=303)
            # The API and assets share the origin at different paths. Only the
            # dashboard route guards interpret this cookie; MCP ignores it.
            response.set_cookie(
                COOKIE_NAME,
                self.cookie,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
                path="/",
            )
            response.headers["Cache-Control"] = "no-store"
            return response
        return HTMLResponse(self.login_page(""), headers={"Cache-Control": "no-store"})

    @staticmethod
    def login_page(message: str) -> str:
        return (
            '<!doctype html><html lang="en"><title>Operator dashboard login</title><h1>Operator dashboard login</h1><p>Ask the operator for this server’s current dashboard access phrase. This login does not authorize MCP or hardware control.</p><p>'
            + escape(message)
            + '</p><form method="post" action="/dashboard/login"><label>Access phrase <input name="code" type="password" required autocomplete="off"></label><button>Open dashboard</button></form></html>'
        )

    async def operator(self, request: Request) -> Response:
        if not self.local(request):
            return Response(
                "Access phrase is shown only on server loopback", status_code=403
            )
        return HTMLResponse(
            '<!doctype html><html lang="en"><title>Dashboard operator access</title><h1>Dashboard operator access</h1><p>Share this code only with the human operator. It is reusable until this HTTP process restarts, not a single-use code.</p><code>'
            + escape(self.token)
            + '</code><p><a href="/dashboard">Open dashboard</a></p></html>',
            headers={"Cache-Control": "no-store"},
        )

    def routes(self) -> list[Route]:
        return [
            Route("/dashboard/login", self.login, methods=["GET", "POST"]),
            Route("/dashboard/operator", self.operator),
        ]

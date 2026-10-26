"""Small operator access boundary for dashboard routes, never MCP authorization."""

from __future__ import annotations

import hmac
import math
import secrets
import sys
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from html import escape
from urllib.parse import parse_qs, urlencode

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
        self._bootstrap_secret: str | None = secrets.token_urlsafe(32)
        self._bootstrap_lock = threading.Lock()
        self.failed_logins: deque[float] = deque(maxlen=5)

    def announce(self, port: int) -> None:
        print(
            f"Operator dashboard access phrase (valid until restart): {self.token}",
            file=sys.stderr,
        )
        bootstrap_url = self.bootstrap_url(port)
        assert bootstrap_url is not None
        print(
            "Operator console one-use link (valid until first successful "
            f"redemption or restart): {bootstrap_url}",
            file=sys.stderr,
        )

    def bootstrap_url(self, port: int) -> str | None:
        with self._bootstrap_lock:
            secret = self._bootstrap_secret
        if secret is None:
            return None
        return f"http://127.0.0.1:{port}/dashboard/bootstrap#{secret}"

    def authorized(self, request: Request) -> bool:
        return hmac.compare_digest(
            request.cookies.get(COOKIE_NAME, "").encode(), self.cookie.encode()
        )

    def _set_cookie(self, response: Response, request: Request) -> None:
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
        inline = request.headers.get("accept") == "application/json"
        selection = urlencode(
            {
                key: request.query_params[key]
                for key in ("run", "archived")
                if key in request.query_params
            }
        )
        suffix = "?" + selection if selection else ""
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
                if inline:
                    return JSONResponse(
                        {"error": "Invalid access phrase."},
                        status_code=401,
                        headers={"Cache-Control": "no-store"},
                    )
                return HTMLResponse(
                    self.login_page("Invalid access phrase.", suffix),
                    status_code=401,
                    headers={"Cache-Control": "no-store"},
                )
            response = (
                JSONResponse({"authorized": True})
                if inline
                else RedirectResponse("/dashboard" + suffix, status_code=303)
            )
            self._set_cookie(response, request)
            return response
        return HTMLResponse(
            self.login_page("", suffix), headers={"Cache-Control": "no-store"}
        )

    @staticmethod
    def login_page(message: str, suffix: str = "") -> str:
        return (
            '<!doctype html><html lang="en"><title>Operator dashboard login</title><h1>Unlock operator dashboard actions</h1><p>Ordinary observations and decisions are public. Ask the operator for this server’s current dashboard access phrase. Login authorizes protected web settings, human controls, internal state and run management; it does not authenticate MCP callers or grant agent control.</p><p>'
            + escape(message)
            + '</p><form method="post" action="/dashboard/login'
            + escape(suffix, quote=True)
            + '"><label>Access phrase <input name="code" type="text" required autocomplete="off" spellcheck="false" maxlength="256"></label><button>Open dashboard</button></form></html>'
        )

    async def operator(self, request: Request) -> Response:
        if not self.authorized(request):
            return Response(
                "Operator login required; the access phrase is printed in the server startup terminal.",
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        return HTMLResponse(
            '<!doctype html><html lang="en"><title>Dashboard operator access</title><h1>Dashboard operator access</h1><p>Share this code only with the human operator. It is reusable until this HTTP process restarts, not a single-use code.</p><code>'
            + escape(self.token)
            + '</code><p><a href="/dashboard">Open dashboard</a></p></html>',
            headers={"Cache-Control": "no-store"},
        )

    async def bootstrap(self, request: Request) -> Response:
        headers = {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; connect-src 'self'",
            "X-Content-Type-Options": "nosniff",
        }
        if request.method == "GET":
            return HTMLResponse(self.bootstrap_page(), headers=headers)
        if request.headers.get("content-type", "").split(";")[0] != (
            "application/x-www-form-urlencoded"
        ):
            return Response(
                "Bootstrap link unavailable.", status_code=401, headers=headers
            )
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > 512:
                return Response(
                    "Bootstrap link unavailable.", status_code=401, headers=headers
                )
        supplied = parse_qs(body.decode("utf-8", errors="replace")).get("secret", [""])[
            0
        ]
        with self._bootstrap_lock:
            expected = self._bootstrap_secret
            accepted = expected is not None and hmac.compare_digest(
                supplied.encode(), expected.encode()
            )
            if accepted:
                self._bootstrap_secret = None
        if not accepted:
            return Response(
                "Bootstrap link unavailable.", status_code=401, headers=headers
            )
        response = Response(status_code=204, headers=headers)
        self._set_cookie(response, request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = headers["Content-Security-Policy"]
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @staticmethod
    def bootstrap_page() -> str:
        return """<!doctype html><html><head><meta name="referrer" content="no-referrer"><title>Operator console bootstrap</title></head><body><p id="status">Opening the operator console…</p><p><a href="/dashboard/login">Use the ordinary phrase login instead</a></p><noscript>JavaScript is required for this one-use link.</noscript><script>
let secret=location.hash.slice(1);
history.replaceState(null,"",location.pathname);
const status=document.getElementById("status");
if(!secret){status.textContent="Bootstrap link unavailable. Use the ordinary phrase login.";}
else{const body=new URLSearchParams({secret});secret="";fetch("/dashboard/bootstrap",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body,credentials:"same-origin",redirect:"error"}).then(response=>{if(!response.ok)throw new Error("denied");location.replace("/console");}).catch(()=>{status.textContent="Bootstrap link unavailable. Use the ordinary phrase login.";});}
</script></body></html>"""

    def routes(self) -> list[Route]:
        return [
            Route("/dashboard/login", self.login, methods=["GET", "POST"]),
            Route("/dashboard/operator", self.operator),
            Route(
                "/dashboard/bootstrap",
                self.bootstrap,
                methods=["GET", "POST"],
            ),
        ]

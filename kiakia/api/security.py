"""Security headers on every API response (defence in depth, OWASP Secure Headers).

The API holds customer names, phone numbers and messages, so browsers are told to:
- never guess a file's type from its content (nosniff), so a message can't be run as a script;
- never show the API inside another site's frame (clickjacking);
- never send our URLs to other sites (Referrer-Policy) or let other sites embed our responses (CORP);
- never keep a copy of personal data in a cache (Cache-Control: no-store on /v1).
API answers are data, not pages, so they get the strictest Content-Security-Policy. /docs is
skipped because Swagger UI needs its own scripts and styles to render.

Written as a plain ASGI middleware: it only adds headers, so it costs microseconds per request.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_ALWAYS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-resource-policy", b"same-origin"),
]
_API_ONLY = [(b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")]
_NO_STORE = (b"cache-control", b"no-store")
_DOCS_PATHS = ("/docs", "/openapi.json")


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        extra = list(_ALWAYS)
        if not path.startswith(_DOCS_PATHS):
            extra += _API_ONLY
        personal_data = path.startswith("/v1/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers += [(name, value) for name, value in extra if name not in present]
                if personal_data and b"cache-control" not in present:
                    headers.append(_NO_STORE)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)

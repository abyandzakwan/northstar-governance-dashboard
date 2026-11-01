"""Loopback-only demo API. This module is never a Lambda handler."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import errno
import json
import os
from portal.auth import AccessDenied, Principal
from portal.service import NotFound, export_bytes, route
from portal.store import get_store


def main():
    os.environ["PORTAL_MODE"] = "demo"
    if os.environ.get("LOCAL_STATE") == ":memory:":
        from portal.demo import seed
        from portal.store import LocalStore

        store = LocalStore(seed=seed())
    else:
        store = get_store()
    principal = Principal("demo.platform-admin", ("gov-portal-admins",))

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            try:
                url = urlparse(self.path)
                path = url.path.removeprefix("/api")
                length = int(self.headers.get("content-length", 0))
                if length > 32768:
                    raise ValueError("Request body too large")
                body = json.loads(self.rfile.read(length)) if length else {}
                if self.command == "GET" and path.startswith("/reports/") and path.endswith("/download"):
                    _, report = route(store, principal, "GET", path.removesuffix("/download"))
                    content = export_bytes(store, report["account_ids"], report["format"])
                    self.send_response(200)
                    self.send_header(
                        "Content-Type", "text/csv" if report["format"] == "csv" else "application/json"
                    )
                    self.send_header(
                        "Content-Disposition", f'attachment; filename="governance.{report["format"]}"'
                    )
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
                status, result = route(
                    store,
                    principal,
                    self.command,
                    path,
                    {k: v[0] for k, v in parse_qs(url.query).items()},
                    body,
                )
            except (ValueError, KeyError, TypeError) as exc:
                status, result = 400, {"error": str(exc)}
            except NotFound as exc:
                status, result = 404, {"error": str(exc)}
            except AccessDenied as exc:
                status, result = 403, {"error": str(exc)}
            except Exception as exc:
                status, result = 500, {"error": str(exc)}
            content = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        do_GET = do_POST = do_DELETE = do_PUT = handle_request

    port = int(os.environ.get("LOCAL_PORT", "8000"))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise SystemExit(
                f"Port {port} is already in use. The demo API may already be running.\n"
                f"Check with: lsof -nP -iTCP:{port} -sTCP:LISTEN\n"
                "Use the existing server or stop it before starting another.\n"
                "To use another port, set LOCAL_PORT and point the frontend's "
                "PORTAL_API_URL to that address."
            ) from None
        raise
    with server:
        print(f"Demo API: http://127.0.0.1:{server.server_port}/api/environments", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nDemo API stopped.", flush=True)


if __name__ == "__main__":
    main()

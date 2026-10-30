"""Attach only to an existing Chrome. No launches, profile writes, or HTTP target APIs.

Chrome >=144's permission endpoint is discovered from DevToolsActivePort, as in
ChromeDevTools/chrome-devtools-mcp src/browser.ts (ensureBrowserConnected).
Page commands use Target.attachToTarget(flatten=True) on the browser WebSocket;
/json/list and synthesised /devtools/page URLs do not work for all such endpoints.
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener


class BrowserAttachError(RuntimeError):
    """A human must resolve this blocker; never recover with another browser."""


HELP = ("Ask the user to enable remote debugging in their already-open Chrome >=144 at "
        "chrome://inspect/#remote-debugging, approve Chrome's connection prompt manually, "
        "and verify the intended logged-in source tabs. Never launch/relaunch Chrome, "
        "switch/create profiles, copy cookies, or bypass login/access checks.")


@dataclass(frozen=True)
class Endpoint:
    websocket_url: str
    source: str

    @property
    def session_key(self) -> str:
        return hashlib.sha256(self.websocket_url.encode()).hexdigest()


def _valid_ws(url: str) -> str:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError:
        port = None
    if (parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not port or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not re.fullmatch(r"/devtools/browser/[A-Za-z0-9_-]+", parsed.path)):
        raise BrowserAttachError("Invalid local browser WebSocket endpoint. " + HELP)
    return url


def discover_endpoint(port: int | str | None = None) -> Endpoint:
    """Default: read exactly one metadata file, without connecting or browsing.

    Old service environments exporting 9223 do not override existing-session mode.
    Legacy fixed ports require an explicit JOBAPPLIER_CDP_MODE=port opt-in, and
    still require manual session verification. No automatic candidate scanning.
    """
    mode = os.environ.get("JOBAPPLIER_CDP_MODE", "existing")
    if mode == "existing":
        root = Path(os.environ.get("JOBAPPLIER_CHROME_USER_DATA_DIR") or
                    (Path.home() / ".config/google-chrome")).expanduser()
        metadata = root / "DevToolsActivePort"
        try:
            if metadata.stat().st_size > 4096:
                raise ValueError("metadata too large")
            lines = [line.strip() for line in metadata.read_text(encoding="utf-8").splitlines() if line.strip()]
            if len(lines) != 2 or not lines[0].isdigit() or not 1 <= int(lines[0]) <= 65535:
                raise ValueError("invalid metadata")
            url = _valid_ws(f"ws://127.0.0.1:{int(lines[0])}{lines[1]}")
        except (OSError, ValueError, UnicodeError) as exc:
            raise BrowserAttachError(f"Cannot discover existing Chrome from {metadata} ({type(exc).__name__}). " + HELP) from exc
        return Endpoint(url, str(metadata))
    if mode == "port":
        raw = str(os.environ.get("JOBAPPLIER_CDP_PORT") or port or "")
        if not str(raw).isdigit() or not 1 <= int(raw) <= 65535:
            raise BrowserAttachError("Explicit port mode requires JOBAPPLIER_CDP_PORT. " + HELP)
        try:
            # Ignore proxy variables for this strictly loopback request.
            with build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{int(raw)}/json/version", timeout=5) as response:
                data = json.loads(response.read(65536))
            url = _valid_ws(data["webSocketDebuggerUrl"])
            if urlsplit(url).port != int(raw):
                raise ValueError("endpoint port mismatch")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise BrowserAttachError("Cannot attach to the explicitly selected Chrome port. " + HELP) from exc
        return Endpoint(url, f"explicit port {raw}")
    raise BrowserAttachError("JOBAPPLIER_CDP_MODE must be existing or port. " + HELP)


def require_confirmation(endpoint: Endpoint) -> None:
    if os.environ.get("JOBAPPLIER_CONFIRMED_BROWSER") != endpoint.session_key:
        raise BrowserAttachError("Existing browser session is not manually verified (or has changed). "
                                "Run python3 -m job_research.browser_attach inspect, then ask the user to verify "
                                "the source tabs/account before setting JOBAPPLIER_CONFIRMED_BROWSER "
                                "to that session_key. " + HELP)


def _connect(endpoint: Endpoint):
    try:
        import websocket
        return websocket.create_connection(endpoint.websocket_url, timeout=30, suppress_origin=True,
                                           http_no_proxy=["127.0.0.1", "localhost", "::1"])
    except Exception as exc:
        raise BrowserAttachError("Existing Chrome connection unavailable, denied, or permission timed out. " + HELP) from exc


def _send(ws, payload: str) -> None:
    try:
        ws.send(payload)
    except Exception as exc:
        raise BrowserAttachError("Existing Chrome transport failed; stop rather than reconnect. " + HELP) from exc


def _receive(ws) -> dict:
    try:
        message = json.loads(ws.recv())
        if not isinstance(message, dict):
            raise ValueError("invalid CDP response")
        return message
    except Exception as exc:
        raise BrowserAttachError("Existing Chrome disconnected or response timed out; stop rather than reconnect. " + HELP) from exc


def _rpc(ws, identifier: int, method: str, params: dict | None = None, session: str | None = None) -> dict:
    message = {"id": identifier, "method": method, "params": params or {}}
    if session:
        message["sessionId"] = session
    _send(ws, json.dumps(message))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = _receive(ws)
        if response.get("id") == identifier and response.get("sessionId") == session:
            if "error" in response:
                raise BrowserAttachError(f"Chrome refused {method}. " + HELP)
            return response.get("result", {})
    raise BrowserAttachError("Chrome permission/command timed out. " + HELP)


def source_host(url: str, source: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in {source, "www." + source}


def select_target(targets: list[dict], source: str) -> dict:
    pages = [target for target in targets if target.get("type") == "page"
             and source_host(str(target.get("url", "")), source)]
    if len(pages) != 1:
        raise BrowserAttachError(f"Expected exactly one existing {source} tab; found {len(pages)}. "
                                "Ask the user to leave one intended source tab open; no arbitrary-tab fallback. " + HELP)
    return pages[0]


ACCESS_MARKERS = ("captcha", "datadome", "verify it's you", "verify it’s you",
                  "security verification", "access is temporarily restricted", "unusual activity",
                  "one more step before you proceed", "authwall", "too many requests")
LOGIN_PATH = re.compile(r"/(?:login|sign-?in|signup|authwall|checkpoint)(?:/|$|\?)", re.I)


def check_snapshot(snapshot: dict, source: str, *, require_login: bool = False) -> None:
    url = str(snapshot.get("url", ""))
    text = str(snapshot.get("text", "")).lower()
    if not source_host(url, source) or LOGIN_PATH.search(url) or any(m in (url + "\n" + text).lower() for m in ACCESS_MARKERS):
        raise BrowserAttachError(f"Wrong {source} session, login wall, or access check. " + HELP)
    if re.search(r"(?:sign in|log in) to (?:continue|linkedin|wellfound)|email or phone|new to linkedin\?", text):
        raise BrowserAttachError(f"{source} requires manual login. " + HELP)
    if require_login and not snapshot.get("logged_in"):
        raise BrowserAttachError(f"Cannot verify logged-in {source} UI. " + HELP)


# Only visible DOM indicators; never read cookies, storage, credentials, or other tabs.
SNAPSHOT_EXPRESSION = """(() => ({url: location.href, text: document.body?.innerText || '',
logged_in: [...document.querySelectorAll('a[href],button')].some(el => {
 if (!el.getClientRects().length) return false;
 const href = el.getAttribute('href') || '';
 const label = (el.innerText || el.getAttribute('aria-label') || '').trim();
 return /^(?:Me|My profile|Your profile|Profile|Log out|Sign out)$/i.test(label) ||
 /\\/(?:messaging|notifications|my-items|mynetwork|talent\\/profile)(?:\\/|$|\\?)/.test(href);
})}))()"""


def check_api_response(result: dict) -> None:
    """A healthy DOM does not make an API login/access/rate-limit response safe."""
    if not isinstance(result, dict) or not isinstance(result.get("status"), int) or not 200 <= result["status"] < 300:
        raise BrowserAttachError("Source API request failed or was restricted. " + HELP)
    data = result.get("data")
    if data is None:
        try:
            data = json.loads(result.get("text", ""))
        except (ValueError, TypeError) as exc:
            raise BrowserAttachError("Source API returned a non-JSON response or access page. " + HELP) from exc
    if not isinstance(data, dict) or data.get("errors") or data.get("parseError") or "data" not in data:
        raise BrowserAttachError("Source API returned errors or an unexpected response. " + HELP)


class PageSocket:
    """WebSocket-shaped adapter for existing single-page consumers.

    Adds flattened sessionId to commands, filters unrelated sessions, and never
    issues Browser.close/Target.closeTarget. The process owns the connection;
    page consumers release it without triggering another approval handshake.
    """
    def __init__(self, ws, session: str, source: str):
        self.ws, self.session, self.source = ws, session, source
        self._guard_id = 1000000000

    def assert_safe(self, *, require_login: bool = False) -> None:
        deadline = time.monotonic() + 8
        while True:
            self._guard_id += 1
            result = _rpc(self.ws, self._guard_id, "Runtime.evaluate",
                          {"expression": SNAPSHOT_EXPRESSION, "returnByValue": True}, self.session)
            snapshot = result.get("result", {}).get("value")
            if not isinstance(snapshot, dict) or result.get("exceptionDetails"):
                raise BrowserAttachError("Cannot inspect current source page safely. " + HELP)
            # Navigation can finish before the authenticated SPA header hydrates.
            # Explicit login/access checks still stop on the first observation.
            check_snapshot(snapshot, self.source)
            if not require_login or snapshot.get("logged_in") or time.monotonic() >= deadline:
                check_snapshot(snapshot, self.source, require_login=require_login)
                return
            time.sleep(0.25)

    def send(self, payload: str) -> None:
        message = json.loads(payload)
        if message.get("method", "").startswith(("Browser.", "Target.")):
            raise BrowserAttachError("Browser/target lifetime commands are forbidden for an attached page")
        if message.get("method") == "Page.navigate" and not source_host(message.get("params", {}).get("url", ""), self.source):
            raise BrowserAttachError("Refusing navigation outside the verified source tab")
        # Check before each navigation or UI mutation/evaluation, including after
        # a redirect. This makes an access check latch a stop before the next action.
        if message.get("method") == "Page.navigate" or message.get("method", "").startswith("Input.") or message.get("method") == "Runtime.evaluate":
            self.assert_safe(require_login=True)
        message["sessionId"] = self.session
        _send(self.ws, json.dumps(message))

    def recv(self) -> str:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            message = _receive(self.ws)
            if message.get("sessionId") == self.session:
                return json.dumps(message)
        raise BrowserAttachError("Chrome page response timed out. " + HELP)

    def close(self) -> None:
        # Sequential fetchers release pages after every URL. Retain the session
        # until process exit, including across source preflight.
        pass


_connections: dict[str, Any] = {}
_pages: dict[tuple[str, str], PageSocket] = {}


def close_connections() -> None:
    """Release our transports, never Chrome or its tabs."""
    connections = list(_connections.values())
    _connections.clear()
    _pages.clear()
    for ws in connections:
        ws.close()


atexit.register(close_connections)


def attach_page(source: str, port: int | str | None = None) -> PageSocket:
    endpoint = discover_endpoint(port)
    require_confirmation(endpoint)
    key = endpoint.session_key
    page_key = (key, source)
    if page_key in _pages:
        page = _pages[page_key]
        page.assert_safe(require_login=True)
        return page
    ws = _connections.get(key)
    if ws is None:
        ws = _connect(endpoint)
        _connections[key] = ws
    try:
        targets = _rpc(ws, 1, "Target.getTargets").get("targetInfos", [])
        target = select_target(targets, source)
        session = _rpc(ws, 2, "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})["sessionId"]
        page = PageSocket(ws, session, source)
        page.assert_safe(require_login=True)
        _pages[page_key] = page
        return page
    except Exception as exc:
        ws.close()
        _connections.pop(key, None)
        for cached_key in list(_pages):
            if cached_key[0] == key:
                del _pages[cached_key]
        if isinstance(exc, BrowserAttachError):
            raise
        raise BrowserAttachError("Existing Chrome permission/session attachment failed. " + HELP) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["discover", "inspect", "preflight", "check-config"])
    args = parser.parse_args()
    try:
        endpoint = discover_endpoint()
        output = {"source": endpoint.source, "session_key": endpoint.session_key,
                  "connected": False, "identity_verified": False}
        if args.command == "check-config":
            require_confirmation(endpoint)
        elif args.command == "inspect":
            ws = _connect(endpoint)  # May show Chrome permission UI. Human approves.
            try:
                targets = _rpc(ws, 1, "Target.getTargets").get("targetInfos", [])
                output["source_tabs"] = [{"targetId": t["targetId"], "host": urlsplit(t["url"]).hostname}
                                         for t in targets if t.get("type") == "page" and
                                         any(source_host(t.get("url", ""), s) for s in ("wellfound.com", "linkedin.com"))]
                output["connected"] = True
            finally:
                ws.close()
        elif args.command == "preflight":
            for source in ("wellfound.com", "linkedin.com"):
                page = attach_page(source)
                page.close()
            output.update(connected=True, identity_verified=True)
        print(json.dumps(output, indent=2))
        return 0
    except BrowserAttachError as exc:
        print("STOP: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

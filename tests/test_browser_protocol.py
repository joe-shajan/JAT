"""Exercise real local WebSocket framing; never connect to the user's browser."""
import base64
import hashlib
import json
import os
import socketserver
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from job_research import browser_attach


class Handler(socketserver.StreamRequestHandler):
    @property
    def test_server(self) -> "Server":
        return cast("Server", self.server)

    def handle(self):
        first = self.rfile.readline().decode().strip()
        headers = {}
        while True:
            line = self.rfile.readline().decode().strip()
            if not line:
                break
            key, value = line.split(":", 1)
            headers[key.lower()] = value.strip()
        if "upgrade" not in headers:
            self.wfile.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            return
        self.test_server.paths.append(first)
        accept = base64.b64encode(hashlib.sha1(
            (headers["sec-websocket-key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
        ).digest()).decode()
        self.wfile.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                          "Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n").encode())
        while True:
            prefix = self.rfile.read(2)
            if len(prefix) != 2 or prefix[0] & 15 == 8:
                return
            length = prefix[1] & 127
            if length == 126:
                length = struct.unpack("!H", self.rfile.read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.rfile.read(8))[0]
            mask = self.rfile.read(4)
            raw = self.rfile.read(length)
            message = json.loads(bytes(value ^ mask[i % 4] for i, value in enumerate(raw)))
            self.test_server.commands.append(message)
            method = message["method"]
            if method == "Target.getTargets":
                result = {"targetInfos": [{"type": "page", "targetId": "source-tab",
                                            "url": "https://wellfound.com/jobs"}]}
            elif method == "Target.attachToTarget":
                result = {"sessionId": "source-session"}
            elif method == "Runtime.evaluate":
                result = {"result": {"value": {"url": "https://wellfound.com/jobs",
                                                "text": "My profile", "logged_in": True}}}
            else:
                result = {}
            response = {"id": message["id"], "result": result}
            if "sessionId" in message:
                response["sessionId"] = message["sessionId"]
            payload = json.dumps(response).encode()
            framing = bytes([129, len(payload)]) if len(payload) < 126 else bytes([129, 126]) + struct.pack("!H", len(payload))
            self.wfile.write(framing + payload)


class Server(socketserver.ThreadingTCPServer):
    paths: list[str]
    commands: list[dict]
    allow_reuse_address = True
    daemon_threads = True


class BrowserProtocolTests(unittest.TestCase):
    def test_real_websocket_discovery_and_flattened_page_command(self):
        with Server(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as directory:
            server.paths = []
            server.commands = []
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            metadata = Path(directory) / "DevToolsActivePort"
            metadata.write_text(f"{server.server_address[1]}\n/devtools/browser/protocol-test\n")
            try:
                with patch.dict(os.environ, {"JOBAPPLIER_CDP_MODE": "existing",
                                              "JOBAPPLIER_CHROME_USER_DATA_DIR": directory}):
                    endpoint = browser_attach.discover_endpoint()
                    with patch.dict(os.environ, {"JOBAPPLIER_CONFIRMED_BROWSER": endpoint.session_key}):
                        page = browser_attach.attach_page("wellfound.com")
                        try:
                            page.send(json.dumps({"id": 42, "method": "Page.enable"}))
                            response = json.loads(page.recv())
                            self.assertEqual(response["id"], 42)
                            self.assertEqual(response["sessionId"], "source-session")
                        finally:
                            page.close()
                self.assertEqual(server.paths, ["GET /devtools/browser/protocol-test HTTP/1.1"])
                attach = next(c for c in server.commands if c["method"] == "Target.attachToTarget")
                self.assertTrue(attach["params"]["flatten"])
                forbidden = {"Browser.close", "Target.closeTarget", "Target.createTarget"}
                self.assertFalse(forbidden.intersection(c["method"] for c in server.commands))
            finally:
                browser_attach.close_connections()
                server.shutdown()
                thread.join(timeout=2)

"""Existing-session Chrome must not ask for approval on every fetched page."""
import json
import os
import unittest
from unittest.mock import patch

from job_research import browser_attach


class BrowserConnectionLifetimeTests(unittest.TestCase):
    def test_sequential_page_fetches_reuse_approved_browser_connection(self):
        browser_attach.close_connections()
        self.addCleanup(browser_attach.close_connections)
        endpoint = browser_attach.Endpoint(
            "ws://127.0.0.1:9222/devtools/browser/lifetime-test", "test metadata"
        )

        class Socket:
            def __init__(self):
                self.pending = []
                self.closed = False

            def send(self, raw):
                message = json.loads(raw)
                method = message["method"]
                results = {
                    "Target.getTargets": {"targetInfos": [{
                        "type": "page", "targetId": "wellfound-tab",
                        "url": "https://wellfound.com/jobs",
                    }]},
                    "Target.attachToTarget": {"sessionId": "wellfound-session"},
                    "Runtime.evaluate": {"result": {"value": {
                        "url": "https://wellfound.com/jobs", "text": "My profile",
                        "logged_in": True,
                    }}},
                }
                response = {"id": message["id"], "result": results.get(method, {})}
                if "sessionId" in message:
                    response["sessionId"] = message["sessionId"]
                self.pending.append(json.dumps(response))

            def recv(self):
                return self.pending.pop(0)

            def close(self):
                self.closed = True

        sockets = []

        def connect(_endpoint):
            socket = Socket()
            sockets.append(socket)
            return socket

        with patch.object(browser_attach, "discover_endpoint", return_value=endpoint), \
                patch.object(browser_attach, "_connect", side_effect=connect) as handshake, \
                patch.dict(os.environ, {"JOBAPPLIER_CONFIRMED_BROWSER": endpoint.session_key}):
            first = browser_attach.attach_page("wellfound.com")
            first.close()
            second = browser_attach.attach_page("wellfound.com")
            second.close()
            self.assertEqual(handshake.call_count, 1,
                             "Every new handshake may require another Chrome permission dialog")
            self.assertFalse(sockets[0].closed,
                             "Closing a page handle must not close the run's approved connection")

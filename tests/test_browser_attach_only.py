import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_research import browser_attach as browser

ROOT = Path(__file__).resolve().parents[1]


class FakeChrome:
    """Protocol fake, not live browser evidence. No HTTP page-target API exists."""
    def __init__(self, targets=None, snapshot=None, reject=None):
        self.targets = targets if targets is not None else [
            {"type": "page", "targetId": "wf", "url": "https://wellfound.com/jobs"}]
        self.snapshot = snapshot if snapshot is not None else {
            "url": "https://wellfound.com/jobs", "text": "Jobs My profile", "logged_in": True}
        self.reject = reject
        self.messages = []
        self.responses = []
        self.closed = False

    def send(self, raw):
        message = json.loads(raw)
        self.messages.append(message)
        method = message["method"]
        response = {"id": message["id"]}
        if "sessionId" in message:
            response["sessionId"] = message["sessionId"]
        if method == self.reject:
            response["error"] = {"message": "denied"}
        elif method == "Target.getTargets":
            response["result"] = {"targetInfos": self.targets}
        elif method == "Target.attachToTarget":
            response["result"] = {"sessionId": "page-session"}
        elif method == "Runtime.evaluate":
            response["result"] = {"result": {"value": self.snapshot}}
        else:
            response["result"] = {}
        self.responses.append(json.dumps(response))

    def recv(self):
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        browser.close_connections()
        self.addCleanup(browser.close_connections)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = mock.patch.dict(os.environ, {"JOBAPPLIER_CHROME_USER_DATA_DIR": str(self.root)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def metadata(self, text="43127\n/devtools/browser/test-existing-session\n"):
        (self.root / "DevToolsActivePort").write_text(text)

    def test_file_discovery_uses_browser_ws_and_ignores_stale_ports_without_connecting(self):
        self.metadata()
        with mock.patch.dict(os.environ, {"JOBAPPLIER_CDP_PORT": "9223", "JOBAPPLIER_LINKEDIN_CDP_PORT": "9222"}), mock.patch.object(browser, "_connect") as connect, mock.patch.object(browser, "build_opener") as http:
            endpoint = browser.discover_endpoint(9222)
        self.assertEqual(endpoint.websocket_url, "ws://127.0.0.1:43127/devtools/browser/test-existing-session")
        self.assertEqual(endpoint.source, str(self.root / "DevToolsActivePort"))
        self.assertEqual(len(endpoint.session_key), 64)
        connect.assert_not_called()
        http.assert_not_called()

    def test_missing_metadata_does_not_create_profile_or_try_port(self):
        with mock.patch.object(browser, "build_opener") as http, self.assertRaisesRegex(browser.BrowserAttachError, "Ask the user"):
            browser.discover_endpoint(9223)
        http.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_malformed_metadata_fails_closed(self):
        for text in ("", "9223", "0\n/devtools/browser/x", "65536\n/devtools/browser/x", "99x\n/devtools/browser/x",
                     "9223\n/devtools/page/x", "9223\n//remote.example/x", "9223\n/devtools/browser/x?token=secret",
                     "9223\n/devtools/browser/x\nextra", "x" * 4097):
            with self.subTest(text=text[:40]):
                self.metadata(text)
                with self.assertRaises(browser.BrowserAttachError):
                    browser.discover_endpoint()

    def test_unknown_mode_and_missing_explicit_port_fail(self):
        for mode in ("auto", "port"):
            with mock.patch.dict(os.environ, {"JOBAPPLIER_CDP_MODE": mode}), self.assertRaises(browser.BrowserAttachError):
                browser.discover_endpoint()

    def test_legacy_port_requires_explicit_mode_and_browser_version_ws(self):
        opener = mock.MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = json.dumps({
            "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/explicit"}).encode()
        with mock.patch.dict(os.environ, {"JOBAPPLIER_CDP_MODE": "port", "JOBAPPLIER_CDP_PORT": "9223"}), mock.patch.object(browser, "build_opener", return_value=opener):
            self.assertEqual(browser.discover_endpoint().source, "explicit port 9223")
        self.assertEqual(opener.open.call_args.args[0], "http://127.0.0.1:9223/json/version")

    def test_unverified_or_changed_session_cannot_connect(self):
        self.metadata()
        with mock.patch.object(browser, "_connect") as connect:
            with self.assertRaisesRegex(browser.BrowserAttachError, "not manually verified"):
                browser.attach_page("wellfound.com")
            os.environ["JOBAPPLIER_CONFIRMED_BROWSER"] = browser.discover_endpoint().session_key
            self.metadata("43127\n/devtools/browser/different-session\n")
            with self.assertRaises(browser.BrowserAttachError):
                browser.attach_page("wellfound.com")
            connect.assert_not_called()

    def attach(self, fake):
        self.metadata()
        os.environ["JOBAPPLIER_CONFIRMED_BROWSER"] = browser.discover_endpoint().session_key
        with mock.patch.object(browser, "_connect", return_value=fake):
            return browser.attach_page("wellfound.com")

    def test_flattened_attach_routes_commands_and_disconnects_only(self):
        fake = FakeChrome()
        page = self.attach(fake)
        self.assertEqual(fake.messages[0]["method"], "Target.getTargets")
        self.assertEqual(fake.messages[1]["params"], {"targetId": "wf", "flatten": True})
        page.send(json.dumps({"id": 1, "method": "Page.enable"}))
        self.assertEqual(fake.messages[-1]["sessionId"], "page-session")
        self.assertEqual(json.loads(page.recv())["id"], 1)
        fake.responses.extend([json.dumps({"id": 4, "sessionId": "other"}), json.dumps({"id": 4, "sessionId": "page-session"})])
        self.assertEqual(json.loads(page.recv())["sessionId"], "page-session")
        page.close()
        self.assertFalse(fake.closed)
        browser.close_connections()
        self.assertTrue(fake.closed)
        self.assertFalse(any(m["method"] in {"Browser.close", "Target.closeTarget", "Target.createTarget"} for m in fake.messages))

    def test_permission_rejection_disconnects_without_page_action(self):
        fake = FakeChrome(reject="Target.attachToTarget")
        with self.assertRaisesRegex(browser.BrowserAttachError, "Ask the user"):
            self.attach(fake)
        self.assertTrue(fake.closed)
        self.assertEqual([m["method"] for m in fake.messages], ["Target.getTargets", "Target.attachToTarget"])

    def test_wrong_absent_and_ambiguous_source_tabs_never_fall_back(self):
        valid = {"type": "page", "url": "https://wellfound.com/jobs", "targetId": "wf"}
        for targets in ([], [{**valid, "url": "https://evil.example/wellfound.com"}],
                        [{**valid, "url": "https://wellfound.com.evil.example/"}], [valid, valid]):
            fake = FakeChrome(targets=targets)
            with self.assertRaises(browser.BrowserAttachError):
                self.attach(fake)
            self.assertTrue(fake.closed)
            self.assertEqual([m["method"] for m in fake.messages], ["Target.getTargets"])

    def test_login_access_check_and_unverified_ui_stop_before_navigation(self):
        for snapshot in (
            {"url": "https://wellfound.com/login", "text": "", "logged_in": True},
            {"url": "https://wellfound.com/jobs", "text": "DataDome", "logged_in": True},
            {"url": "https://wellfound.com/jobs", "text": "Jobs", "logged_in": False},
        ):
            fake = FakeChrome(snapshot=snapshot)
            with self.assertRaises(browser.BrowserAttachError):
                self.attach(fake)
            self.assertTrue(fake.closed)
            self.assertNotIn("Page.navigate", [m["method"] for m in fake.messages])

    def test_new_access_check_prevents_next_mutation_and_browser_close_forbidden(self):
        fake = FakeChrome()
        page = self.attach(fake)
        fake.snapshot["text"] = "Verify it's you"
        with self.assertRaises(browser.BrowserAttachError):
            page.send(json.dumps({"id": 7, "method": "Page.navigate", "params": {"url": "https://wellfound.com/jobs"}}))
        self.assertNotIn("Page.navigate", [m["method"] for m in fake.messages])
        with self.assertRaises(browser.BrowserAttachError):
            page.send(json.dumps({"id": 8, "method": "Browser.close"}))
        page.close()


    def test_transport_failure_stops_before_navigation_without_reconnection(self):
        fake = FakeChrome()
        page = self.attach(fake)
        with mock.patch.object(fake, "recv", side_effect=TimeoutError), mock.patch.object(browser, "_connect") as reconnect:
            with self.assertRaises(browser.BrowserAttachError):
                page.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": "https://wellfound.com/jobs"}}))
            reconnect.assert_not_called()
        self.assertNotIn("Page.navigate", [m["method"] for m in fake.messages])


    def test_lost_login_and_off_source_destination_prevent_actions(self):
        fake = FakeChrome()
        page = self.attach(fake)
        for logged_in, destination in ((False, "https://wellfound.com/jobs"), (True, "https://unrelated.example/")):
            fake.snapshot["logged_in"] = logged_in
            with self.assertRaises(browser.BrowserAttachError):
                page.send(json.dumps({"id": 3, "method": "Page.navigate", "params": {"url": destination}}))
        self.assertNotIn("Page.navigate", [m["method"] for m in fake.messages])


class AttachOnlyTests(unittest.TestCase):
    def test_graphql_consumers_stop_on_api_only_restrictions(self):
        for filename, function in (("cdp_browser.py", "save_graphql_page"),):
            spec = importlib.util.spec_from_file_location("api_guard_" + filename.replace(".", "_"), ROOT / "scripts" / filename)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            self.addCleanup(sys.modules.pop, spec.name, None)
            spec.loader.exec_module(module)
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "response.json"
                args = (1, output) if function == "save_graphql_page" else (1,)
                for response in ({"status": 403, "text": "Denied"}, {"status": 429, "data": {}}, {"status": 200, "text": "<html>captcha</html>"}, {"status": 200, "data": {"errors": [{"message": "Unauthenticated"}]}}):
                    page = mock.Mock()
                    page.evaluate.return_value = response
                    with self.subTest(filename=filename, response=response), self.assertRaises(browser.BrowserAttachError):
                        getattr(module, function)(page, *args)
                    page.evaluate.assert_called_once()
                    self.assertFalse(output.exists())
                browser.check_api_response({"status": 200, "data": {"data": {"talent": {}}}})

    def test_unavailable_browser_stops_without_launching_or_running_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            scripts = workspace / "scripts"
            scripts.mkdir()
            shutil.copy2(ROOT / "scripts/run-daily-dashboard-update.sh", scripts)
            package = workspace / "job_research"
            package.mkdir()
            (package / "__init__.py").write_text("")
            shutil.copy2(ROOT / "job_research/browser_attach.py", package)
            marker = workspace / "unexpected-action"
            launcher = scripts / "launch-jobapplier-chrome.sh"
            launcher.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
            launcher.chmod(0o755)
            bin_dir = workspace / "bin"
            bin_dir.mkdir()
            for name, body in {
                "curl": f'touch "{marker}"; exit 7',
                "google-chrome": f'touch "{marker}"',
                "python3": (f'if [ "$1 $2" = "-m job_research.browser_attach" ]; then exec "{sys.executable}" "$@"; fi\n'
                            f'touch "{marker}"; exit 99'),
            }.items():
                stub = bin_dir / name
                stub.write_text(f"#!/bin/sh\n{body}\n")
                stub.chmod(0o755)
            env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": directory,
                   "JOBAPPLIER_CHROME_USER_DATA_DIR": str(workspace / "missing-profile"),
                   "JOBAPPLIER_CDP_PORT": "9223", "PYTHONDONTWRITEBYTECODE": "1"}
            result = subprocess.run(["bash", str(scripts / "run-daily-dashboard-update.sh")],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("Ask the user", result.stderr)
            self.assertIn("DevToolsActivePort", result.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse((workspace / ".daily-dashboard.lock").exists())
            self.assertFalse((workspace / "missing-profile").exists())

    def test_legacy_launcher_is_disabled(self):
        result = subprocess.run(["bash", str(ROOT / "scripts/launch-jobapplier-chrome.sh")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("launching is disabled", result.stderr)

    def test_fetcher_without_port_never_launches_chrome(self):
        from job_research.wellfound import ChromeDumpFetcher
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(browser, "attach_page", side_effect=browser.BrowserAttachError("Ask the user")) as attach, mock.patch("subprocess.run") as launch:
            fetcher = ChromeDumpFetcher(ROOT, mock.Mock())
            with self.assertRaises(browser.BrowserAttachError):
                fetcher.fetch_url("https://wellfound.com/jobs", "search", "test")
            attach.assert_called_once_with("wellfound.com", None)
            launch.assert_not_called()

    def test_all_cdp_consumers_use_shared_attachment(self):
        from job_research.linkedin_jobs import LinkedInCdpFetcher
        with mock.patch("job_research.linkedin_jobs.attach_page") as attach:
            attach.return_value.recv.side_effect = [json.dumps({"id": i, "result": {}}) for i in (1, 2)]
            with LinkedInCdpFetcher(mock.Mock(), 9223):
                pass
            attach.assert_called_once_with("linkedin.com", 9223)
        # Mock socket responses explicitly in script consumers to exercise wiring.
        for filename, constructor, args, source in (
            ("cdp_browser.py", "get_page", (), "wellfound.com"),
        ):
            with self.subTest(filename=filename):
                spec = importlib.util.spec_from_file_location("attach_test_" + filename.replace(".", "_"), ROOT / "scripts" / filename)
                assert spec is not None and spec.loader is not None
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                self.addCleanup(sys.modules.pop, spec.name, None)
                spec.loader.exec_module(module)
                socket = mock.Mock()
                socket.recv.side_effect = [json.dumps({"id": i, "result": {}}) for i in range(1, 5)]
                with mock.patch.object(module, "attach_page", return_value=socket) as attach:
                    page = getattr(module, constructor)(*args)
                    page.close()
                self.assertEqual(attach.call_args.args[0], source)
                socket.close.assert_called_once()

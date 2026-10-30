import unittest
from unittest.mock import Mock, patch

from job_research import browser_attach as browser


class LoginHydrationTests(unittest.TestCase):
    def test_job_description_checkpoints_are_not_security_checkpoint_urls(self):
        snapshot = {'url': 'https://wellfound.com/jobs/example', 'text': 'Build memory systems, checkpoints, and task-tracking.', 'logged_in': True}
        browser.check_snapshot(snapshot, 'wellfound.com', require_login=True)
        snapshot['url'] = 'https://wellfound.com/checkpoint'
        with self.assertRaises(browser.BrowserAttachError):
            browser.check_snapshot(snapshot, 'wellfound.com', require_login=True)

    def test_waits_for_authenticated_header_but_never_waits_past_access_check(self):
        page = browser.PageSocket(Mock(), 'session', 'wellfound.com')
        def response(logged_in=False, text='Jobs'):
            return {'result': {'value': {'url': 'https://wellfound.com/jobs', 'text': text, 'logged_in': logged_in}}}
        with patch.object(browser, '_rpc', side_effect=[response(), response(True)]) as rpc, patch.object(browser.time, 'sleep'):
            page.assert_safe(require_login=True)
            self.assertEqual(rpc.call_count, 2)
        with patch.object(browser, '_rpc', return_value=response(text='Verify it’s you')) as rpc, patch.object(browser.time, 'sleep') as wait:
            with self.assertRaises(browser.BrowserAttachError):
                page.assert_safe(require_login=True)
            rpc.assert_called_once()
            wait.assert_not_called()

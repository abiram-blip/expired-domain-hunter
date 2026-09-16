import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
import chat_notify


class ChatNotifyTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"GCHAT_DOMAIN_HUNT_WEBHOOK_URL": "https://example.invalid/messages?key=secret&token=secret", "EDH_RUN_DATE": "2026-09-16"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def ok(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
        return Response()

    def test_preserves_fields_and_links_and_valid_identity(self):
        with patch('urllib.request.urlopen', return_value=self.ok()) as send:
            self.assertTrue(chat_notify.post_chat('• <https://example.invalid/auction|*example.com*> — Tier A — $5 — ends tomorrow (fit) :rotating_light: URGENT'))
        req = send.call_args.args[0]
        data = json.loads(req.data)
        for content in ['example.com', 'https://example.invalid/auction', 'Tier A', '$5', 'ends tomorrow', '(fit)', '🚨 URGENT']:
            self.assertIn(content, data['text'])
        query = parse_qs(urlsplit(req.full_url).query)
        self.assertEqual(query['key'], ['secret'])
        self.assertNotIn('requestId', query)
        self.assertRegex(query['messageId'][0], r'^client-[0-9a-f]{56}$')
        self.assertEqual(set(data), {'text'})

    def test_retry_same_identity_different_day_distinct(self):
        with patch('urllib.request.urlopen', return_value=self.ok()) as send:
            chat_notify.post_chat('Same'); chat_notify.post_chat('Same')
            with patch.dict(os.environ, {'EDH_RUN_DATE':'2026-09-17'}): chat_notify.post_chat('Same')
        urls=[c.args[0].full_url for c in send.call_args_list]
        self.assertEqual(urls[0], urls[1]); self.assertNotEqual(urls[0], urls[2])

    def test_distinct_events_same_text_remain_separate(self):
        with patch('urllib.request.urlopen', return_value=self.ok()) as send:
            chat_notify.post_chat('Failure', event_key='run-1'); chat_notify.post_chat('Failure', event_key='run-2')
        self.assertNotEqual(send.call_args_list[0].args[0].full_url,send.call_args_list[1].args[0].full_url)

    def test_already_exists_is_success_only_for_matching_error(self):
        for status, expected in [('ALREADY_EXISTS',True),('ABORTED',False)]:
            error=HTTPError('https://example.invalid',409,'Conflict',{},io.BytesIO(json.dumps({'error':{'status':status}}).encode()))
            with patch('urllib.request.urlopen', side_effect=error), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(chat_notify.post_chat('Test'),expected)

    def test_timeout_retry_reuses_identity(self):
        with patch('urllib.request.urlopen', side_effect=[TimeoutError('secret'),self.ok()]) as send, contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(chat_notify.post_chat('Test')); self.assertTrue(chat_notify.post_chat('Test'))
        self.assertEqual(send.call_args_list[0].args[0].full_url,send.call_args_list[1].args[0].full_url)

    def test_failures_do_not_leak_webhook_or_raise(self):
        for error in [URLError('https://example.invalid?token=secret'), HTTPError('secret',429,'Limit',{},io.BytesIO(b'{}'))]:
            output=io.StringIO()
            with patch('urllib.request.urlopen',side_effect=error), contextlib.redirect_stderr(output): self.assertFalse(chat_notify.post_chat('Test'))
            self.assertNotIn('secret',output.getvalue())

    def test_missing_secret_skips_network(self):
        with patch.dict(os.environ,{},clear=True), patch('urllib.request.urlopen') as send, contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(chat_notify.post_chat('Test')); send.assert_not_called()

    def test_shortlist_sender_preserves_actual_row_fields(self):
        import hunt
        import tempfile
        row = [''] * 19
        for index, value in {0:'example.com',2:'https://example.invalid/auction',3:'brand fit',4:'clean history',5:'2026-09-18',17:'$4 URGENT',18:'A'}.items():
            row[index] = value
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as data:
            json.dump([row], data); data.flush()
            with patch.object(hunt, 'cfg', return_value={}), patch('urllib.request.urlopen', return_value=self.ok()) as send, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(hunt.chat_post(data.name, 'Delivery note'), 0)
        text = json.loads(send.call_args.args[0].data)['text']
        for expected in ['example.com','https://example.invalid/auction','brand fit','clean history','2026-09-18','$4 URGENT','Tier A','Delivery note','🚨 URGENT']:
            self.assertIn(expected, text)

    def test_all_pipeline_senders_use_helper_and_no_legacy_secret(self):
        from pathlib import Path
        root=Path(__file__).parent
        for name in ['hunt.py','run_pipeline.py','sync_pipeline.py','name_judge.py']:
            source=(root/name).read_text();self.assertIn('post_chat',source);self.assertNotIn('SLACK_WEBHOOK_URL',source)
        for name in ['daily-hunt.yml','daily-sync.yml']:
            source=(root/'.github/workflows'/name).read_text();self.assertIn('python3 chat_notify.py',source);self.assertNotIn('SLACK_WEBHOOK_URL',source)


if __name__ == '__main__': unittest.main()

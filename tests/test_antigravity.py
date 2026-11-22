import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    'antigravity_quotas', Path(__file__).resolve().parents[1] / 'src' / 'fetch_quotas.py'
)
quotas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quotas)

DESKTOP_SERVER = '/Applications/Antigravity.app/Contents/Resources/bin/language_server'
IDE_SERVER = ('/Applications/Antigravity IDE.app/Contents/Resources/app/extensions/'
              'antigravity/bin/language_server_macos_arm')
PROCESS_LIST = ('/bin/ps', '-axww', '-o', 'uid=,pid=,comm=')
DESKTOP_PID = 500
IDE_PID = 501
HTTP_PORT = 50200
HTTPS_PORT = 50201
FIXTURE_TOKEN = 'dummy-csrf-fixture'
SUMMARY = {'groups': [{'displayName': 'Gemini Models', 'buckets': [
    {'bucketId': 'gemini-5h', 'window': '5h', 'remainingFraction': 0.6},
]}]}


def listening_command(pid):
    return ('/usr/sbin/lsof', '-nP', '-a', '-p', str(pid), '-i4TCP', '-sTCP:LISTEN', '-Fn')


def args_command(pid):
    return ('/bin/ps', '-ww', '-p', str(pid), '-o', 'args=')


def process_line(pid, path):
    return f'{os.getuid()} {pid} {path}\n'


def listening_output(pid, *ports):
    return f'p{pid}\n' + ''.join(f'f{fd}\nn127.0.0.1:{port}\n' for fd, port in enumerate(ports, 7))


class AntigravityDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.daemon = self.home / '.gemini' / 'antigravity' / 'daemon'
        self.daemon.mkdir(mode=0o700, parents=True)

    def write_registry(self, http_port=HTTP_PORT, https_port=HTTPS_PORT, pid=DESKTOP_PID):
        path = self.daemon / f'hub_{http_port}.json'
        state = {'pid': pid, 'httpPort': http_port, 'httpsPort': https_port, 'csrfToken': FIXTURE_TOKEN}
        path.write_text(json.dumps(state))
        path.chmod(0o600)
        return path

    def run_discovery(self, outputs, summary=SUMMARY):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout=outputs[tuple(command)], stderr='')

        with patch.object(quotas.Path, 'home', return_value=self.home), \
             patch.object(quotas.subprocess, 'run', side_effect=run) as commands, \
             patch.object(quotas, 'query_agy_summary', return_value=summary) as query:
            response = quotas.retrieve_agy_summary()
        return response, commands, query

    def test_current_desktop_uses_verified_registry_without_reading_legacy_args(self):
        self.write_registry()
        outputs = {
            PROCESS_LIST: process_line(DESKTOP_PID, DESKTOP_SERVER),
            listening_command(DESKTOP_PID): listening_output(DESKTOP_PID, HTTP_PORT, HTTPS_PORT),
        }
        response, commands, query = self.run_discovery(outputs)
        self.assertEqual(response, SUMMARY)
        query.assert_called_once_with(HTTPS_PORT, FIXTURE_TOKEN)
        self.assertNotIn(args_command(DESKTOP_PID), [tuple(call.args[0]) for call in commands.call_args_list])

    def test_ignores_stale_registry_entries_and_unsafe_files(self):
        outputs = {
            PROCESS_LIST: process_line(DESKTOP_PID, DESKTOP_SERVER),
            listening_command(DESKTOP_PID): listening_output(DESKTOP_PID, HTTP_PORT, HTTPS_PORT),
            args_command(DESKTOP_PID): f'{DESKTOP_SERVER} --standalone\n',
        }
        for mutation in ('stale pid', 'unmatched HTTPS port', 'unmatched registry filename',
                         'mismatched registry HTTP port',
                         'world readable', 'symlink', 'malformed JSON'):
            with self.subTest(mutation=mutation):
                if mutation == 'stale pid':
                    path = self.write_registry(pid=600)
                elif mutation == 'unmatched HTTPS port':
                    path = self.write_registry(https_port=50301)
                elif mutation == 'unmatched registry filename':
                    path = self.write_registry(http_port=50300)
                elif mutation == 'mismatched registry HTTP port':
                    path = self.write_registry(http_port=50300).rename(self.daemon / f'hub_{HTTP_PORT}.json')
                elif mutation == 'world readable':
                    path = self.write_registry()
                    path.chmod(0o644)
                elif mutation == 'symlink':
                    state = self.write_registry()
                    target = state.rename(self.home / 'other.json')
                    path = state
                    path.symlink_to(target)
                else:
                    path = self.write_registry()
                    path.write_text('{')

                try:
                    response, _, query = self.run_discovery(outputs)
                    self.assertIsNone(response)
                    query.assert_not_called()
                finally:
                    path.unlink()

    def test_uses_legacy_ide_when_desktop_is_absent(self):
        outputs = {
            PROCESS_LIST: process_line(IDE_PID, IDE_SERVER),
            listening_command(IDE_PID): listening_output(IDE_PID, HTTPS_PORT),
            args_command(IDE_PID): f'{IDE_SERVER} --csrf_token={FIXTURE_TOKEN}\n',
        }
        response, _, query = self.run_discovery(outputs)
        self.assertEqual(response, SUMMARY)
        query.assert_called_once_with(HTTPS_PORT, FIXTURE_TOKEN)

    def test_does_not_switch_to_ide_when_desktop_quota_lookup_fails(self):
        self.write_registry()
        outputs = {
            PROCESS_LIST: process_line(IDE_PID, IDE_SERVER) + process_line(DESKTOP_PID, DESKTOP_SERVER),
            listening_command(DESKTOP_PID): listening_output(DESKTOP_PID, HTTP_PORT, HTTPS_PORT),
            args_command(DESKTOP_PID): f'{DESKTOP_SERVER} --standalone\n',
        }
        response, commands, query = self.run_discovery(outputs, summary=None)
        self.assertIsNone(response)
        query.assert_called_once_with(HTTPS_PORT, FIXTURE_TOKEN)
        checked_commands = [tuple(call.args[0]) for call in commands.call_args_list]
        self.assertNotIn(listening_command(IDE_PID), checked_commands)
        self.assertNotIn(args_command(IDE_PID), checked_commands)


class AntigravityResponseTests(unittest.TestCase):
    def test_rejects_malformed_payloads_and_redirects(self):
        bad_payloads = (b'not JSON', b'[]', b'{"response": []}', b'{"response": {"groups": {}}}')
        valid_payload = json.dumps({'response': SUMMARY}).encode()
        cases = [(200, body) for body in bad_payloads] + [(302, valid_payload), (200, valid_payload)]
        for status, body in cases:
            with self.subTest(status=status, body=body):
                connection = Mock()
                response = io.BytesIO(body)
                response.status = status
                connection.getresponse.return_value = response
                with patch.object(quotas.http.client, 'HTTPSConnection', return_value=connection) as connect:
                    result = quotas.query_agy_summary(HTTPS_PORT, FIXTURE_TOKEN)
                self.assertEqual(result, SUMMARY if status == 200 and body == valid_payload else None)
                self.assertEqual(connect.call_args.args[:2], ('127.0.0.1', HTTPS_PORT))
                method, route, content, headers = connection.request.call_args.args
                self.assertEqual(method, 'POST')
                self.assertTrue(route.endswith('/RetrieveUserQuotaSummary'))
                self.assertEqual(content, b'{}')
                self.assertEqual(headers['x-codeium-csrf-token'], FIXTURE_TOKEN)
                connection.close.assert_called_once_with()

    def test_canonical_gemini_group_is_selected_before_argon(self):
        summary = {'groups': [
            {'displayName': 'Gemini 4 Argon', 'buckets': [
                {'bucketId': 'gemini-argon-wtu-weekly', 'window': 'weekly', 'remainingFraction': 0.45},
            ]},
            {'displayName': 'Gemini Models', 'buckets': [
                {'bucketId': 'gemini-5h', 'window': '5h', 'remainingFraction': 0},
                {'bucketId': 'gemini-weekly', 'window': 'weekly', 'remainingFraction': 0.85},
            ]},
            {'displayName': 'Claude and GPT models', 'buckets': [
                {'bucketId': '3p-weekly', 'window': 'weekly', 'remainingFraction': 0.33},
            ]},
        ]}
        result = quotas.parse_agy_summary(summary, {})
        self.assertTrue(result['ok'])
        self.assertEqual(result['gemini_5h_rem'], 0)
        self.assertEqual(result['gemini_weekly_rem'], 85)
        self.assertEqual(result['claude_weekly_rem'], 33)
        self.assertIsNone(result['claude_5h_rem'])


if __name__ == '__main__':
    unittest.main()

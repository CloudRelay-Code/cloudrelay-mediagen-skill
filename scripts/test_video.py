"""Offline protocol and credential-boundary tests; no paid API calls."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import generate_video as video
import update
import check_update

MP4 = b'\x00\x00\x00\x18ftypisom' + b'\0' * 32
SPEC = {'id': 'grok-imagine-video', 'kind': 'video', 'endpoint': 'grok_videos', 'async': True}
DONE = {'status': 'done', 'video': {'url': '/v1/videos/job/content', 'duration': 8}, 'usage': {'seconds': 8}}


class Response(io.BytesIO):
    def __init__(self, data=MP4, length=None):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data) if length is None else length)}


class VideoTests(unittest.TestCase):
    def args(self, *args):
        return video.build_parser().parse_args(list(args))

    def test_generation_defaults_and_explicit_parameters(self):
        payload = video.build_payload(self.args('--prompt', 'sunrise'))
        self.assertEqual(payload, {'prompt': 'sunrise', 'duration': 8, 'resolution': '480p'})
        payload = video.build_payload(self.args('--prompt', 'sunrise', '--duration', '15', '--resolution', '1080p', '--aspect-ratio', '9:16', '--image', 'https://example.com/frame.png'))
        self.assertEqual(payload['image']['url'], 'https://example.com/frame.png')
        self.assertEqual((payload['duration'], payload['resolution'], payload['aspect_ratio']), (15, '1080p', '9:16'))

    def test_edit_and_extension_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.mp4'
            source.write_bytes(MP4)
            for action, expected in [('edit', None), ('extend', 6)]:
                payload = video.build_payload(self.args('--action', action, '--prompt', 'continue', '--video', str(source)))
                self.assertEqual(payload.get('duration'), expected)
                self.assertTrue(payload['video']['url'].startswith('data:video/mp4;base64,'))
                self.assertNotIn('resolution', payload)
                self.assertNotIn('aspect_ratio', payload)

    def test_local_first_frame_is_encoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'frame.png'
            source.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\0' * 20)
            payload = video.build_payload(self.args('--prompt', 'animate', '--image', str(source)))
            self.assertTrue(payload['image']['url'].startswith('data:image/png;base64,'))

    def test_invalid_inputs_stop_before_credentials_or_network(self):
        cases = [[], ['--prompt', ' '], ['--prompt', 'x', '--duration', '0'],
                 ['--prompt', 'x', '--image', 'missing-file.png'],
                 ['--action', 'edit', '--prompt', 'x'],
                 ['--action', 'extend', '--prompt', 'x', '--video', 'https://example.com/a.mp4', '--duration', '11'],
                 ['--action', 'edit', '--prompt', 'x', '--video', 'https://example.com/a.mp4', '--resolution', '720p'],
                 ['--request-id', 'job', '--prompt', 'x']]
        with patch.object(video.shared, '_api_key') as key, patch.object(video.shared, '_request') as request, contextlib.redirect_stderr(io.StringIO()):
            for argv in cases:
                with self.subTest(argv=argv):
                    self.assertEqual(video.main(argv), 1)
            key.assert_not_called()
            request.assert_not_called()

    def test_model_discovery_and_image_only_restrictions(self):
        args = self.args('--prompt', 'x')
        spec = dict(SPEC, id='grok-imagine-video-1.5')
        with patch.object(video.shared, '_request', return_value=(200, {'data': [spec]})):
            with self.assertRaises(video.Error):
                video.discover(args, 'test-key')
            args.image = 'frame.png'
            self.assertEqual(video.discover(args, 'test-key')[0], spec)
            args.action = 'edit'
            with self.assertRaises(video.Error):
                video.discover(args, 'test-key')
        for changed in [dict(SPEC, endpoint='images'), dict(SPEC, kind='image'), dict(SPEC, **{'async': False})]:
            with patch.object(video.shared, '_request', return_value=(200, {'data': [changed]})), self.assertRaises(video.Error):
                video.discover(self.args('--prompt', 'x', '--model', SPEC['id']), 'test-key')

    def test_poll_pending_transient_and_done(self):
        replies = [video.Error('network'), (503, {}), (202, {'status': 'pending'}), (200, DONE)]
        with patch.object(video.shared, '_request', side_effect=replies) as request, patch.object(video.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(video.poll('job/123', 'test-key', 10), DONE)
            self.assertTrue(all(c.args[:2] == ('GET', '/v1/videos/job%2F123') for c in request.call_args_list))

    def test_poll_terminal_http_errors_stop_immediately(self):
        for code in (401, 403, 429, 404):
            with patch.object(video.shared, '_request', return_value=(code, {})) as request, self.assertRaises(video.Error):
                video.poll('job', 'test-key', 10)
            self.assertEqual(request.call_count, 1)

    def test_poll_terminal_states_and_timeout(self):
        for status in video.FAILED:
            with patch.object(video.shared, '_request', return_value=(200, {'status': status})), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(video.Error):
                video.poll('job', 'test-key', 10)
        with patch.object(video.time, 'monotonic', side_effect=[0, 2]), patch.object(video.shared, '_request') as request, self.assertRaises(video.Error):
            video.poll('job', 'test-key', 1)
        request.assert_not_called()

    def test_download_credential_origin_boundary(self):
        for url, authenticated in [('/v1/videos/job/content', True), ('https://cloudrelay.cn:443/v1/videos/job/content', True), ('https://cdn.example.com/video.mp4', False), ('https://cloudrelay.cn.evil.example/a', False), ('https://cloudrelay.cn:8443/a', False), ('http://cloudrelay.cn/a', False)]:
            with tempfile.TemporaryDirectory() as tmp, patch.object(video.shared._NO_REDIRECT_OPENER, 'open', return_value=Response()) as opener:
                path = Path(tmp) / 'video.mp4'
                self.assertEqual(video.download_video(url, 'test-key', path), len(MP4))
                self.assertEqual(path.read_bytes(), MP4)
                self.assertEqual(opener.call_args.args[0].get_header('Authorization'), 'Bearer test-key' if authenticated else None)

    def test_bad_downloads_do_not_produce_outputs(self):
        responses = [Response(b'<html>Error</html>'), Response(length=len(MP4) + 100)]
        for response in responses:
            with tempfile.TemporaryDirectory() as tmp, patch.object(video.shared._NO_REDIRECT_OPENER, 'open', return_value=response):
                path = Path(tmp) / 'video.mp4'
                with self.assertRaises(video.Error):
                    video.download_video('/v1/videos/job/content', 'test-key', path)
                self.assertFalse(path.exists())
                self.assertFalse(path.with_suffix('.mp4.part').exists())
        with self.assertRaises(video.Error):
            video.shared._RejectRedirectHandler().redirect_request(None, None, 302, '', {}, 'https://other.example/a')

    def test_end_to_end_each_action_submits_once_and_reports(self):
        for action in ('generate', 'edit', 'extend'):
            with tempfile.TemporaryDirectory() as tmp:
                argv = ['--action', action, '--prompt', 'test-key sunrise', '--output-dir', tmp]
                if action != 'generate':
                    argv += ['--video', 'https://example.com/source.mp4']
                replies = [(200, {'data': [SPEC]}), (202, {'request_id': '../job'}), (202, {'status': 'pending'}), (200, DONE)]
                out = io.StringIO()
                with patch.object(video.shared, '_api_key', return_value=('test-key', 'test-source')) as key, patch.object(video.shared, '_request', side_effect=replies) as request, patch.object(video.shared._NO_REDIRECT_OPENER, 'open', return_value=Response()), patch.object(video.time, 'sleep'), contextlib.redirect_stdout(out):
                    self.assertEqual(video.main(argv), 0)
                key.assert_called_once_with('grok', with_source=True)
                posts = [c for c in request.call_args_list if c.args[0] == 'POST']
                self.assertEqual(len(posts), 1)
                self.assertEqual(posts[0].args[1], video.ENDPOINTS[action])
                report = json.loads(out.getvalue().split('PARAMETERS_JSON=')[1])
                self.assertEqual(report['action'], action)
                self.assertEqual(report['duration_is_extension'], action == 'extend')
                self.assertFalse(report['playback_verified'])
                self.assertEqual(Path(report['outputs'][0]).parent, Path(tmp).resolve())
                self.assertNotIn('test-key', out.getvalue())

    def test_resume_only_polls_and_downloads(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(video.shared, '_api_key', return_value=('test-key', 'test-source')), patch.object(video.shared, '_request', return_value=(200, DONE)) as request, patch.object(video.shared._NO_REDIRECT_OPENER, 'open', return_value=Response()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(video.main(['--request-id', 'job', '--output-dir', tmp]), 0)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[:2], ('GET', '/v1/videos/job'))

    def test_failed_submission_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(video.shared, '_api_key', return_value=('test-key', 'test-source')), patch.object(video.shared, '_request', side_effect=[(200, {'data': [SPEC]}), video.Error('timeout')]) as request, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(video.main(['--prompt', 'x', '--output-dir', tmp]), 1)
            self.assertEqual(request.call_count, 2)

    def test_legacy_target_does_not_fetch_or_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / 'SKILL.md').write_text('---\nname: cloudrelay-imagegen\ndescription: Legacy\n---\n', encoding='utf-8')
            for module, argv in ((update, ['--auto']), (check_update, ['--json'])):
                with patch.object(module, 'fetch_latest_release') as fetch, contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(module.main(argv + ['--target-dir', str(target)]), 1)
                    fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()

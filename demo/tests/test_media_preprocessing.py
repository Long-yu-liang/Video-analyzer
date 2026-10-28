"""Short inputs keep their complete picture stream; all model responses are fixtures."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from video_analyzer.media import processing as media
from test_support import MockModelTestCase


class MediaPreprocessingChecks(MockModelTestCase):
    def setUp(self):
        self.meta = {'duration_s': 22.856833, 'width': 1280, 'height': 720, 'has_audio': True,
                     'video_codec': 'h264', 'video_duration_s': 22.768, 'video_start_s': 0.003,
                     'video_frame_count': 692}

    def test_remux_has_no_seek_no_reencode_and_error_keeps_exit_code(self):
        with patch.object(media, 'run') as run:
            media.strip_audio(Path('input.mp4'), Path('visual.mp4'))
        args = run.call_args.args[0]
        self.assertEqual(args[args.index('-c:v')+1], 'copy')
        self.assertNotIn('-ss', args)
        self.assertNotIn('-t', args)
        self.assertIn('-an', args)
        failure = subprocess.CalledProcessError(8987, 'ffmpeg', stderr=b'encoder stopped')
        with patch.object(media.subprocess, 'run', side_effect=failure), self.assertRaises(media.MediaProcessingError) as caught:
            media.run(['ffmpeg', '-c:v', 'libx264', 'out.mp4'])
        self.assertEqual(caught.exception.detail['operation'], 'transcode')
        self.assertEqual(caught.exception.detail['exit_code'], 8987)
        self.assertEqual(caught.exception.detail['stderr'], 'encoder stopped')
        self.assertIn('重新编码', str(caught.exception))

    def test_missing_tool_timeout_and_empty_stderr_are_explicit(self):
        for error, code in ((FileNotFoundError(), 'missing_tool'),
                            (subprocess.TimeoutExpired('ffmpeg', 90), 'timeout'),
                            (subprocess.CalledProcessError(8987, 'ffmpeg'), 'process_failed')):
            with self.subTest(code=code), patch.object(media.subprocess, 'run', side_effect=error), \
                    self.assertRaises(media.MediaProcessingError) as caught:
                media.run(['ffmpeg', '-c:v', 'libx264', 'out.mp4'])
            self.assertEqual(caught.exception.detail['code'], code)

    def test_full_video_verifies_timing_frame_count_and_audio_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            path, output = Path(folder)/'input.mp4', Path(folder)/'visual.mp4'
            path.write_bytes(b'fixture source')
            output.write_bytes(b'fixture remux')
            actual = {**self.meta, 'has_audio': False, 'duration_s': 22.771}
            remux = lambda *_: output
            prepared, metadata, method = media.prepare_full_video(path, output, self.meta, 'direct',
                remux=remux, prober=lambda _: actual)
            self.assertEqual(prepared, output)
            self.assertEqual(method, 'stream_copy_without_audio')
            self.assertEqual(metadata['video_frame_count'], 692)
            for changed in ({'video_start_s': 8.3}, {'video_duration_s': 14.5}, {'video_frame_count': 440}, {'has_audio': True}):
                with self.subTest(changed=changed), self.assertRaises(media.MediaProcessingError) as caught:
                    media.prepare_full_video(path, output, self.meta, 'direct', remux=remux,
                        prober=lambda _: {**actual, **changed})
                self.assertEqual(caught.exception.detail['code'], 'timeline_changed')
            self.assertEqual(path.read_bytes(), b'fixture source')

    def test_silent_direct_and_frame_inputs_do_not_invoke_ffmpeg(self):
        path = Path('input.mp4')
        with patch.object(media, 'strip_audio') as remux, patch.object(media, 'probe') as probe:
            direct = media.prepare_full_video(path, Path('out.mp4'), {**self.meta, 'has_audio': False}, 'direct')
            frames = media.prepare_full_video(path, Path('out.mp4'), self.meta, 'frames')
        self.assertEqual(direct[0], path)
        self.assertEqual(direct[2], 'original_video')
        self.assertEqual(frames[2], 'original_frames')
        remux.assert_not_called(); probe.assert_not_called()

    def test_preprocess_failure_is_saved_and_never_calls_model(self):
        failure = media.MediaProcessingError('remux', exit_code=8987)
        with patch.dict(app.MODELS, {'qwen3-vl-plus': 'beeroute'}), \
                patch.dict(os.environ, {'BEEROUTE_API_KEY': 'fixture-no-network'}), \
                patch.object(app, 'probe', return_value=self.meta), \
                patch.object(app, 'strip_audio', side_effect=failure), patch.object(app, 'call_v13_model') as model, \
                patch.object(app, 'clip_video') as transcode, patch.object(app, 'save_record') as saved:
            result = app.analyze_events(b'fixture', 'fixture.mp4', 'v14')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['media_error']['exit_code'], 8987)
        self.assertEqual(result['segments'][0]['preprocessing_status'], 'failed')
        self.assertEqual(result['segments'][0]['preprocessing_method'], 'stream_copy_without_audio')
        self.assertEqual(result['model_request_count'], 0)
        model.assert_not_called(); transcode.assert_not_called(); saved.assert_called_once()

    def test_short_original_can_still_split_after_model_truncation(self):
        meta = {**self.meta, 'duration_s': 20., 'has_audio': False}
        answer = {'summary': 'local fixture', 'events': [{'start_s': 0, 'end_s': 1, 'observation_s': 0.5,
                  'description': 'fixture', 'evidence': 'fixture', 'uncertainty': 'fixture'}], 'limitations': []}
        def response(url, body, key, record):
            if not response.called:
                response.called = True
                raise app.TruncatedOutputError('fixture truncation')
            return json.dumps(answer)
        response.called = False
        def fake_clip(_, output, *_args):
            output.write_bytes(b'fixture subclip')
        with patch.dict(app.MODELS, {'qwen3-vl-plus': 'beeroute'}), \
                patch.dict(os.environ, {'BEEROUTE_API_KEY': 'fixture-no-network'}), \
                patch.object(app, 'probe', side_effect=[meta, {**meta, 'duration_s': 10.}, {**meta, 'duration_s': 11.}]), \
                patch.object(app, 'clip_video', side_effect=fake_clip) as transcode, \
                patch.object(app, 'call_v13_model', side_effect=response), patch.object(app, 'save_record'):
            result = app.analyze_events(b'fixture', 'fixture.mp4', 'v14')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(transcode.call_count, 2)
        self.assertEqual([s['preprocessing_method'] for s in result['segments']],
                         ['original_video', 'segment_transcode', 'segment_transcode'])

    def test_long_transcode_failure_keeps_segment_error_and_no_model_call(self):
        meta = {**self.meta, 'duration_s': 58.}
        failure = media.MediaProcessingError('transcode', exit_code=8987, stderr=b'fixture encoder exit')
        with patch.dict(app.MODELS, {'qwen3-vl-plus': 'beeroute'}), \
                patch.dict(os.environ, {'BEEROUTE_API_KEY': 'fixture-no-network'}), \
                patch.object(app, 'probe', return_value=meta), patch.object(app, 'clip_video', side_effect=failure), \
                patch.object(app, 'call_v13_model') as model, patch.object(app, 'save_record'):
            result = app.analyze_events(b'fixture', 'fixture.mp4', 'v14')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['media_error']['operation'], 'transcode')
        self.assertEqual(result['segments'][0]['core_end_s'], 20.)
        self.assertEqual(result['segments'][0]['media_error']['exit_code'], 8987)
        self.assertEqual(result['segments'][0]['preprocessing_status'], 'failed')
        self.assertEqual(app.model_request_count(result), 0)
        model.assert_not_called()

    def test_segment_transcode_falls_back_after_libx264_abort(self):
        def fake(args):
            if args[args.index('-c:v') + 1] == 'libx264':
                raise media.MediaProcessingError('transcode', exit_code=8987)
            self.assertEqual(args[args.index('-c:v') + 1], 'h264_mf')
            self.assertIn('2M', args)
            Path(args[-1]).write_bytes(b'fallback-h264')
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'clip.mp4'
            output.write_bytes(b'truncated')
            result = media.clip_video(Path('in.mp4'), output, 0, 20, runner=fake)
            self.assertEqual(result.read_bytes(), b'fallback-h264')

    def test_nonzero_ffmpeg_exit_keeps_a_decodable_segment(self):
        def fake(args):
            data = b'bad' if 'libx264' in args else b'good-segment'
            Path(args[-1]).write_bytes(data)
            raise media.MediaProcessingError('transcode', exit_code=8987)
        def prober(path):
            if path.read_bytes() != b'good-segment':
                raise media.MediaProcessingError('probe', exit_code=1)
            return {'video_codec': 'h264', 'has_audio': False, 'duration_s': 20.0}
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'clip.mp4'
            result = media.clip_video(Path('in.mp4'), output, 0, 20, runner=fake, prober=prober)
            self.assertEqual(result.read_bytes(), b'good-segment')

    def test_segment_transcode_keeps_libx264_error_when_fallback_fails(self):
        def fake(args):
            if 'libx264' in args:
                raise media.MediaProcessingError('transcode', exit_code=8987)
            raise media.MediaProcessingError('transcode', exit_code=1, stderr=b'fallback failed')
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'clip.mp4'
            with self.assertRaises(media.MediaProcessingError) as caught:
                media.clip_video(Path('in.mp4'), output, 0, 20, runner=fake)
        self.assertEqual(caught.exception.detail['exit_code'], 8987)
        self.assertNotIn('fallback failed', str(caught.exception))

    def test_missing_ffmpeg_does_not_try_another_encoder(self):
        calls = []
        def fake(args):
            calls.append(args)
            raise media.MediaProcessingError('transcode', code='missing_tool',
                                              message='请安装 FFmpeg，并将 ffmpeg / ffprobe 加入 PATH。')
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(media.MediaProcessingError):
            media.clip_video(Path('in.mp4'), Path(folder) / 'clip.mp4', 0, 20, runner=fake)
        self.assertEqual(len(calls), 1)

    def test_oversized_libx264_segment_is_rejected_without_fallback(self):
        def fake(args):
            self.assertEqual(args[args.index('-c:v') + 1], 'libx264')
            Path(args[-1]).write_bytes(b'x' * (30 * 1024 * 1024 + 1))
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError, '30 MiB'):
            media.clip_video(Path('in.mp4'), Path(folder) / 'clip.mp4', 0, 20, runner=fake)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg not installed')
    def test_s07_segments_remain_decodable_h264_within_upload_limit(self):
        source = app.ROOT / 'mp4-example' / 'S07_篮球比赛_前58秒.mp4'
        if not source.exists():
            self.skipTest('S07 sample not available')
        with tempfile.TemporaryDirectory() as folder:
            for index, (_, _, start, end) in enumerate(media.segment_windows(58)):
                output = Path(folder) / f'{index}.mp4'
                media.clip_video(source, output, start, end)
                info = media.probe(output)
                self.assertEqual(info['video_codec'], 'h264', output)
                self.assertFalse(info['has_audio'])
                self.assertAlmostEqual(info['duration_s'], end - start, delta=0.15)
                self.assertLessEqual(output.stat().st_size, 12 * 1024 * 1024)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg not installed')
    def test_real_s06_reaches_mock_model_three_times_without_transcoding(self):
        source = app.ROOT/'mp4-example/S06_有声乒乓球.mp4'
        if not source.exists():
            self.skipTest('S06 sample not available')
        raw = source.read_bytes(); source_hash = hashlib.sha256(raw).hexdigest()
        original = app.probe(source)
        answer = json.dumps({'summary': 'local fixture, not real inference', 'events': [], 'limitations': []})
        with patch.dict(app.MODELS, {'qwen3-vl-plus': 'beeroute'}), \
                patch.dict(os.environ, {'BEEROUTE_API_KEY': 'fixture-no-network'}), \
                patch.object(app, 'clip_video', side_effect=AssertionError('Unexpected reencoding')), \
                patch.object(app, 'call_v13_model', return_value=answer) as model, \
                patch.object(app, 'urlopen', side_effect=AssertionError('No real network')), \
                patch.object(app, 'save_record'):
            for _ in range(3):
                result = app.analyze_events(raw, source.name, 'v14')
                self.assertEqual(result['status'], 'success', result.get('error'))
                segment = result['segments'][0]
                self.assertEqual(segment['preprocessing_method'], 'stream_copy_without_audio')
                self.assertFalse(segment['metadata']['has_audio'])
                for key in ('video_start_s', 'video_duration_s', 'video_frame_count'):
                    self.assertEqual(segment['metadata'][key], original[key])
            self.assertEqual(model.call_count, 3)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)


if __name__ == '__main__':
    unittest.main()

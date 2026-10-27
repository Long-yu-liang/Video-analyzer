"""Upload and timeline boundaries; no real media processing or model calls."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from video_analyzer.media import processing
from video_analyzer.paths import MAX_VIDEO, MAX_VIDEO_SECONDS, MAX_MODEL_REQUEST
from video_analyzer.projects.store import Store
from test_projects import state, point


class VideoLimitTests(unittest.TestCase):
    def test_probe_accepts_120_seconds_and_rejects_longer(self):
        for duration in (60, 90, 120, 120.01):
            def runner(args):
                return json.dumps({'format':{'duration':str(duration)},'streams':[
                    {'codec_type':'video','codec_name':'h264','width':1280,'height':720}]}).encode()
            if duration<=120:self.assertEqual(processing.probe(Path('input.mp4'),runner)['duration_s'],duration)
            else:
                with self.assertRaisesRegex(ValueError,'120'):processing.probe(Path('input.mp4'),runner)

    def test_file_boundary_and_model_payload_headroom(self):
        self.assertEqual(MAX_VIDEO,30*1024*1024)
        self.assertEqual(MAX_VIDEO_SECONDS,120)
        self.assertGreater(MAX_MODEL_REQUEST,MAX_VIDEO*4//3+2*1024*1024)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'mp4-example').mkdir();video=root/'mp4-example/test.mp4'
            with video.open('wb') as stream:stream.truncate(MAX_VIDEO)
            self.assertEqual(len(processing.read_sample(root,'test.mp4',MAX_VIDEO)),MAX_VIDEO)
            with video.open('ab') as stream:stream.write(b'x')
            with self.assertRaisesRegex(ValueError,'30 MiB'):processing.read_sample(root,'test.mp4',MAX_VIDEO)

    def test_project_saves_full_120_second_timeline(self):
        with tempfile.TemporaryDirectory() as folder:
            data=state();data['points']=[point()];data['video']['duration_s']=120;data['points'][0]['time_s']=119
            store=Store(Path(folder));saved=store.save({'state':data})
            self.assertEqual(saved['state']['points'][0]['time_s'],119)
            data['video']['duration_s']=121
            with self.assertRaises(ValueError):store.save({'state':data})

    def test_sampling_covers_120_seconds_at_two_fps(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(processing.subprocess,'run',return_value=SimpleNamespace(stderr=b'pts_time:0')) as run:
            (Path(folder)/'001.jpg').write_bytes(b'fixture-image')
            processing.extract(Path('input.mp4'),Path(folder),120,2)
            command=run.call_args.args[0]
            self.assertGreaterEqual(int(command[command.index('-frames:v')+1]),240)

"""Human-readable names must preserve record linkage and never overwrite results."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import app


class NamingChecks(unittest.TestCase):
    def record(self, stage='events', **changes):
        record = {'record_id': app.secrets.token_hex(16), 'model': 'qwen3-vl-plus',
                  'stage': stage, 'mode': 'frames', 'video_name': 'S06_有声乒乓球.mp4', 'status': 'success'}
        record.update(changes)
        return record

    def test_stage_labels_date_and_duplicate_suffix(self):
        class FrozenDate:
            @staticmethod
            def now(tz):
                return datetime(2026, 10, 21, 17, tzinfo=timezone.utc).astimezone(tz)

        with tempfile.TemporaryDirectory() as temp, patch.object(app, 'ROOT', Path(temp)), \
             patch.object(app, 'datetime', FrozenDate):
            for stage, label in [('events', '第一步'), ('opportunities', '第二步'),
                                 ('gameplay', '第三步'), ('gameplay_brief', '玩法描述'), (None, '单阶段分析')]:
                record = self.record(stage)
                path = app.save_record(record)
                self.assertEqual(path.name, f'S06_有声乒乓球__frames__20261022_{label}.json')
                stored = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(stored['record_id'], record['record_id'])
                self.assertEqual(stored['record_file'], record['record_file'])
            second = self.record()
            second_path = app.save_record(second)
            self.assertEqual(second_path.name, 'S06_有声乒乓球__frames__20261022_第一步_2.json')
            self.assertEqual(app.saved_record_paths(second['record_id']), [second_path])

    def test_new_names_link_stages_and_survive_moving_to_nested_folder(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app, 'ROOT', Path(temp)):
            events = self.record()
            original = app.save_record(events)
            moved = original.parent / 'v14' / original.name
            moved.parent.mkdir()
            original.rename(moved)
            self.assertEqual(app.stage_record(events['record_id'])['record_file'], moved.relative_to(Path(temp)).as_posix())
            opportunity = self.record('opportunities', parent_record_id=events['record_id'])
            path = app.save_record(opportunity)
            self.assertEqual(app.load_stage_result(opportunity['record_id'], 'opportunities', '机会')['parent_record_id'], events['record_id'])
            self.assertEqual(app.saved_record_paths(opportunity['record_id']), [path])
            # A duplicate new-format record is detected rather than silently selecting one.
            duplicate = moved.with_name('copy_' + moved.name)
            duplicate.write_bytes(moved.read_bytes())
            with self.assertRaises(ValueError):
                app.stage_record(events['record_id'])

    def test_concurrent_saves_never_overwrite(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app, 'ROOT', Path(temp)):
            records = [self.record() for _ in range(8)]
            with ThreadPoolExecutor(max_workers=4) as pool:
                paths = list(pool.map(app.save_record, records))
            self.assertEqual(len(set(paths)), 8)
            self.assertEqual({json.loads(p.read_text(encoding='utf-8'))['record_id'] for p in paths},
                             {r['record_id'] for r in records})


if __name__ == '__main__':
    unittest.main()

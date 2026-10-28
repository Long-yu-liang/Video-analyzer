"""Mocked workflows must never depend on credentials in a developer's .env."""
import os
import unittest
from unittest.mock import patch
from video_analyzer import settings, application as app
from video_analyzer.paths import ROOT


class MockModelTestCase(unittest.TestCase):
    def run(self, result=None):
        original = settings.Store.read
        def isolated(store):
            if store.path == ROOT / 'config/settings.json':
                return {'schema_version': 1, 'revision': 0,
                        'values': {k: os.environ.get(k, v) for k, v in settings.DEFAULTS.items()}}
            return original(store)
        def models():
            active = settings.CURRENT.get()
            return active['_models'] if active is not None else app.MODELS
        with patch.dict(os.environ, {'BEEROUTE_API_KEY': 'fixture-no-network'}), \
                patch.dict(app.MODELS, {name:'beeroute' for name in settings.DEFAULTS['BEEROUTE_MODELS'].split(',')}), \
                patch.object(settings.Store, 'read', isolated), patch.object(app, 'runtime_models', models):
            return super().run(result)

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from honorboot.discovery import detect_configuration, find_tools


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def profile(self, name, serial, imei):
        p = self.root / 'devices' / name
        p.mkdir(parents=True)
        (p / 'progress.json').write_text(json.dumps(dict(serial=serial, imei=imei)))

    def detect(self, current, devices='PHONE fastboot'):
        with patch('honorboot.discovery.find_tools', return_value=self.root), patch('honorboot.discovery.Transport.run', return_value=(0, devices)):
            return detect_configuration(current, self.root)

    def test_one_saved_seed_is_reused(self):
        self.profile('one', 'PHONE', '490154203237518')
        result = self.detect({})
        self.assertEqual(result['serial'], 'PHONE')
        self.assertEqual(result['imei'], '490154203237518')

    def test_multiple_seeds_require_selection(self):
        self.profile('one', 'PHONE', 'one')
        self.profile('two', 'PHONE', 'two')
        result = self.detect({})
        self.assertEqual(result['imei'], '')
        self.assertEqual(result['imeis'], ['one', 'two'])

    def test_existing_selection_preserved(self):
        self.profile('one', 'PHONE', 'one')
        self.profile('two', 'PHONE', 'two')
        self.assertEqual(self.detect({'serial': 'PHONE', 'imei': 'two'})['imei'], 'two')

    def test_new_device_never_inherits_old_seed(self):
        result = self.detect({'serial': 'OLD', 'imei': 'old-seed'})
        self.assertEqual(result['serial'], 'PHONE')
        self.assertEqual(result['imei'], '')

    def test_multiple_devices_not_guessed(self):
        self.assertNotIn('serial', self.detect({}, 'A fastboot\nB fastboot'))

    def test_adjacent_tools_detected(self):
        tools = self.root / 'platform-tools'
        tools.mkdir()
        for name in ['adb.exe', 'fastboot.exe']: (tools / name).touch()
        self.assertEqual(find_tools(base=self.root), tools.resolve())

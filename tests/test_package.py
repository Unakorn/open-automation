"""Portable packaging and control serialization checks."""
import ast
import importlib
import json
from pathlib import Path
import re
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'work'))


class PackageTests(unittest.TestCase):
    def test_runtime_dependency_closure_imports(self):
        for path in (ROOT / 'work').glob('*.py'):
            with self.subTest(module=path.stem):
                importlib.import_module(path.stem)

    def test_catalog_has_only_expected_plugin_settings_and_no_private_paths(self):
        document = json.loads((ROOT / 'resources/automation-effects.json').read_text())
        self.assertEqual(set(document), {'version', 'description', 'effects', 'controller'})
        self.assertEqual(document['version'], 1)
        self.assertEqual(set(document['effects']), {'cutoff', 'delay', 'reverb'})
        records = [document['controller']] + [effect['events'] for effect in document['effects'].values()]
        for record in records:
            for key, encoded in record:
                payload = bytes.fromhex(encoded)
                texts = [payload.decode('latin1'), payload.decode('utf-16-le', errors='ignore')]
                for text in texts:
                    self.assertNotRegex(text, r'(?i)(?:[a-z]:[\\/]Users[\\/]|@[^\s]+\.[a-z]{2,}|api[_-]?key|password|authorization)')
        name = dict(document['controller'])[203]
        self.assertEqual(bytes.fromhex(name).decode('utf-16-le'), 'Automation Control\0')
        reverb = bytes.fromhex(dict(document['effects']['reverb']['events'])[213])
        self.assertIn(b'presetName="Default Preset"', reverb)
        self.assertIn(b'ValhallaFutureVerb.vst3', reverb)

    def test_native_defaults_have_no_printable_private_state(self):
        from drum_monkey_template import _NATIVE_SUFFIX_HEX
        for key, encoded in _NATIVE_SUFFIX_HEX:
            payload = bytes.fromhex(encoded)
            self.assertFalse(re.search(rb'[A-Za-z:/\\@]{8,}', payload), key)

    def test_production_sources_compile_without_embedded_profile_paths(self):
        for folder in ('work', 'outputs'):
            for path in (ROOT / folder).glob('*.py'):
                text = path.read_text(encoding='utf-8')
                ast.parse(text, filename=path.name)
                self.assertNotRegex(text, r'(?i)[a-z]:[\\/]Users[\\/]')

    def test_sectioning_preserves_every_control_attribute_and_native_order(self):
        from flp_connector import _section_control_notes
        fmt = struct.Struct('<IHHIHHHHBBBB')
        bar = 96 * 4
        end = 17 * bar + 13
        originals = [fmt.pack(0, 16384, cid, end, 60, 321, 654, 987, 12, 103, 34, 56) for cid in (8, 3)]
        sections = _section_control_notes(originals, 96, end)
        self.assertEqual([s['position'] for s in sections], [0, 8 * bar, 16 * bar])
        self.assertEqual([s['duration'] for s in sections], [8 * bar, 8 * bar, bar + 13])
        for cid in (8, 3):
            expected = fmt.unpack(next(p for p in originals if fmt.unpack(p)[2] == cid))
            length = 0
            for section in sections:
                rows = list(fmt.iter_unpack(section['payload']))
                self.assertEqual([r[2] for r in rows], [8, 3])
                row = next(r for r in rows if r[2] == cid)
                self.assertEqual(row[0] + section['position'], length)
                self.assertEqual(row[1:3] + row[4:], expected[1:3] + expected[4:])
                length += row[3]
            self.assertEqual(length, end)


if __name__ == '__main__':
    unittest.main()

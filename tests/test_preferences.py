"""Full-song defaults migrate once without selecting or changing a source FLP."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'outputs/FLP Automation Connector.py'
spec = importlib.util.spec_from_file_location('full_song_gui', SCRIPT)
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)


class FullSongDefaultsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings = Path(folder.name) / 'settings.json'
        self.legacy = Path(folder.name) / 'legacy.json'
        self.enterContext(patch.object(gui, 'SETTINGS', self.settings))
        self.enterContext(patch.object(gui, 'LEGACY_SETTINGS', self.legacy))

    def test_first_launch_is_full_song_without_writing_preferences(self):
        result = gui.load_preferences()
        self.assertEqual(result['options'], dict(gui.FULL_SONG_DEFAULTS, fx_controls=gui.DEFAULT_FX_CONTROLS))
        self.assertEqual(result['options']['movement'], 'song')
        self.assertEqual(result['options']['fx_style'], 'full_range')
        self.assertNotIn('flp_source', result)
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.legacy.exists())

    def test_upgrade_adds_new_fx_without_resetting_saved_intensity_timing_or_folders(self):
        old = {'version': 1, 'source_folder': 'my songs', 'output_folder': 'my copies',
               'options': {'movement': 'groove', 'fx_style': 'fuller', 'intensity': 2,
                           'phrase_bars': 8, 'seed': 23}, 'flp_source': 'old.flp'}
        self.settings.write_text(json.dumps(old), encoding='utf-8')
        before = self.settings.read_bytes()
        result = gui.load_preferences()
        self.assertEqual(result['options']['movement'], 'song')
        self.assertEqual(result['options']['phrase_bars'], 8)
        self.assertEqual(result['options']['intensity'], 2)
        self.assertEqual(result['options']['fx_controls'], gui.DEFAULT_FX_CONTROLS)
        self.assertEqual(result['options']['seed'], 23)
        self.assertEqual(result['source_folder'], 'my songs')
        self.assertEqual(result['output_folder'], 'my copies')
        self.assertNotIn('flp_source', result)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_straight_ramp_connector_migrates_only_style_after_upgrade(self):
        saved = {'defaults_revision': gui.DEFAULTS_REVISION,
                 'options': {'movement': 'groove', 'intensity': .8, 'fx_style': 'original'}}
        self.settings.write_text(json.dumps(saved), encoding='utf-8')
        self.assertEqual(gui.load_preferences()['options'], dict(saved['options'], movement='song', fx_controls=gui.DEFAULT_FX_CONTROLS))
        self.assertEqual(len(gui.MOVEMENTS), 1)

    def test_curve_is_never_restored_from_global_preferences(self):
        saved = {'defaults_revision': gui.DEFAULTS_REVISION,
                 'options': {'movement': 'song', 'intensity': .8, 'energy_curve': {'points': [[0, 1]]}}}
        self.settings.write_text(json.dumps(saved), encoding='utf-8')
        result = gui.load_preferences()['options']
        self.assertNotIn('energy_curve', result)
        self.assertEqual(result['intensity'], .8)

    def test_invalid_saved_fx_falls_back_without_resetting_other_preferences(self):
        saved = {'defaults_revision': gui.DEFAULTS_REVISION, 'source_folder': 'my songs',
                 'options': {'movement': 'song', 'intensity': .83, 'phrase_bars': 8,
                             'fx_controls': {'delay': {'amount': float('nan')}}}}
        self.settings.write_text(json.dumps(saved), encoding='utf-8')
        before = self.settings.read_bytes()
        result = gui.load_preferences()
        self.assertEqual(result['options']['fx_controls'], gui.DEFAULT_FX_CONTROLS)
        self.assertEqual(result['options']['intensity'], .83)
        self.assertEqual(result['options']['phrase_bars'], 8)
        self.assertEqual(result['source_folder'], 'my songs')
        self.assertEqual(self.settings.read_bytes(), before)

    def test_legacy_folder_migrates_but_old_source_is_not_selected(self):
        self.legacy.write_text(json.dumps({'flp_source': str(Path('songs') / 'old.flp'),
                                          'flp_output_folder': 'copies',
                                          'flp_connector_options': {'movement': 'drop'}}), encoding='utf-8')
        before = self.legacy.read_bytes()
        result = gui.load_preferences()
        self.assertEqual(result['source_folder'], str(Path('songs')))
        self.assertEqual(result['options']['movement'], 'song')
        self.assertNotIn('flp_source', result)
        self.assertEqual(self.legacy.read_bytes(), before)

    def test_reset_defaults_changes_controls_without_touching_files(self):
        class Value:
            def set(self, value):
                self.value = value

        class Form:
            def _movement_changed(self):
                self.hint_updated = True

        form = Form()
        for name in ('intensity', 'intensity_label', 'movement', 'phrase_bars', 'drop_bars',
                     'drop_length', 'drop_fx', 'fx_style', 'division', 'sylenth_movement'):
            setattr(form, name, Value())
        gui.FLPConnectorApp.use_full_song_defaults(form)
        self.assertEqual(gui.MOVEMENTS[form.movement.value][0], 'song')
        self.assertEqual(form.intensity.value, 160)
        self.assertEqual(form.phrase_bars.value, '16')
        self.assertEqual(form.drop_bars.value, '')
        self.assertEqual(gui.DROP_FX[form.drop_fx.value], 'reduced')
        self.assertEqual(gui.FX_STYLES[form.fx_style.value], 'full_range')
        self.assertTrue(form.hint_updated)
        self.assertFalse(self.settings.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)

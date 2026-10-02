"""Hidden-Tk checks for the saved-FLP connector; preferences stay in scratch."""
from pathlib import Path
from datetime import datetime
import copy
import hashlib
import importlib.util
import json
import os
import sys
import types
import unittest
from unittest.mock import patch
from collections import Counter

import tempfile
HERE = Path(__file__).resolve().parent
APP = HERE.parent
spec = importlib.util.spec_from_file_location('flp_only_gui', APP / 'outputs/FLP Automation Connector.py')
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

@unittest.skipUnless(os.environ.get('OPEN_AUTOMATION_GUI_TESTS') == '1', 'Enable hidden Tk checks with OPEN_AUTOMATION_GUI_TESTS=1')
class FLPConnectorGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='open-automation-gui-')
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.run_dir = Path(cls.scratch.name)
        cls.protected = {str(path): digest(path) for path in (
            APP / 'outputs/energy_painter.py', APP / 'work/energy_curve.py')}

    def setUp(self):
        self.folder = self.run_dir / self._testMethodName
        self.folder.mkdir()
        self.settings = self.folder / 'settings.json'
        self.legacy_settings = self.folder / 'legacy_settings.json'
        self.legacy = {'template': 'legacy.flp', 'template_sha256': 'legacy hash',
                       'part_assignments': {'keys': 3, 'pluck': 2}, 'drum_preset': {'path': 'saved kit'},
                       'output_folder': 'legacy outputs', 'user_extra': 'preserve me',
                       'flp_source': str(self.folder / 'Old selected song.flp'),
                       'flp_output_folder': str(self.folder / 'old output'),
                       'flp_connector_options': {'intensity': 1.6}}
        self.legacy_settings.write_text(json.dumps(self.legacy), encoding='utf-8')
        self.before_settings = self.legacy_settings.read_bytes()
        self.settings_patch = patch.object(gui, 'SETTINGS', self.settings)
        self.settings_patch.start(); self.addCleanup(self.settings_patch.stop)
        self.enterContext(patch.object(gui, 'LEGACY_SETTINGS', self.legacy_settings))
        self.errors = self.enterContext(patch.object(gui.messagebox, 'showerror'))
        self.infos = self.enterContext(patch.object(gui.messagebox, 'showinfo'))
        self.app = gui.FLPConnectorApp(); self.app.withdraw()
        self.addCleanup(lambda: self.app.destroy() if not self.app._closed else None)
        self.source = self.folder / 'User song.flp'
        self.source.write_bytes(b'FLhd GUI fixture, not a native FLP; parser stubbed in contract checks.\0')
        self.app.out_path.set(str(self.folder / 'exports'))

    def tearDown(self):
        self.assertEqual(self.legacy_settings.read_bytes(), self.before_settings)
        for path, expected in self.protected.items():
            self.assertEqual(digest(path), expected, path)

    def report(self, count=3, supported=True):
        return {'supported': supported, 'errors': [] if supported else ['Unsupported timeline record.'],
                'warnings': ['One shared mixer route has limited coverage.'],
                'instruments': [{'id': i, 'name': f'User instrument {i+1}', 'plugin': 'Actual saved plugin',
                                 'mixer_insert': i + 1, 'automation_status': 'available' if i else 'skipped',
                                 'lanes': ['delay', 'reverb'] if i else [],
                                 **({'reason': 'Shared insert is left unchanged.'} if i == 0 else {})}
                                for i in range(count)], 'tempo': 137.5, 'bars': 64,
                'sha256': digest(self.source),
                'energy_overview': {'source_sha256': digest(self.source), 'ticks_per_beat': 96,
                                    'end_tick': 64 * 384, 'title': self.source.stem, 'bars': 64,
                                    'bar_ticks': 384, 'duration_seconds': 112, 'paintable': True,
                                    'ticks': list(range(0, 64 * 384 + 1, 384)),
                                    'activity': [.5] * 65, 'markers': []}}

    def load(self, report=None):
        self.app.flp_path.set(str(self.source))
        if self.app._inspect_after is not None:
            self.app.after_cancel(self.app._inspect_after); self.app._inspect_after = None
        self.app.loading = False
        self.app._preview_path = self.source.resolve()
        self.app._source_signature = gui.file_signature(self.source)
        self.app._show_preview(report or self.report())

    def test_starts_flp_only_without_legacy_template_or_startup_write(self):
        self.assertEqual(self.app.flp_path.get(), '')
        self.assertEqual(self.app.intensity.get(), 160)
        self.assertEqual(self.app.movement.get(), 'Full song — straight ramps')
        self.assertEqual(len(gui.MOVEMENTS), 1)
        self.assertFalse(self.settings.exists())
        self.assertEqual(self.app.source_folder, str(self.folder))
        self.assertFalse(self.app.loading)
        self.assertIsNone(self.app._inspect_after)
        self.assertTrue(self.app.generate_button.instate(['disabled']))
        self.assertFalse(hasattr(self.app, 'midi_path'))
        self.assertFalse(hasattr(self.app, 'template_path'))
        self.assertFalse(hasattr(self.app, 'mapping_vars'))
        self.assertFalse(hasattr(self.app, 'drum_preset_info'))

    def test_dynamic_coverage_for_one_seven_and_twenty_instruments(self):
        for count in (1, 7, 20):
            with self.subTest(count=count):
                self.load(self.report(count))
                self.assertEqual(len(self.app.instrument_table.get_children()), count)
                self.assertFalse(self.app.generate_button.instate(['disabled']))
                self.assertIn(str(count) + ' instrument', self.app.preview_status.get())
        self.assertIn('Shared insert', self.app.instrument_detail.get())
        self.assertIn('limited coverage', self.app.preview_notice.get())

    def test_unsupported_project_disables_export_and_explains_why(self):
        self.load(self.report(21, False))
        self.assertTrue(self.app.generate_button.instate(['disabled']))
        self.assertIn('Unsupported timeline', self.app.preview_notice.get())
        with self.assertRaisesRegex(ValueError, 'cannot be exported'):
            self.app._generation_options()

    def test_drum_monkey_shows_verified_automatic_output_notice(self):
        report = self.report(1)
        report['instruments'][0].update(name='DRUMS', plugin='Unison Drum Monkey', mixer_insert=29,
                                        automation_status='available', lanes=['cutoff', 'delay', 'reverb'],
                                        drum_routing={'status': 'auto_saved_split'})
        self.load(report)
        self.assertIn('stays one instrument', self.app.preview_notice.get())
        self.assertIn('Mixer 30', self.app.preview_notice.get())
        self.assertIn('Automatically routes the drums to Mixer 29', self.app.preview_notice.get())
        self.assertNotIn('Automatic drum routing is unavailable', self.app.preview_notice.get())
        self.assertNotIn('drum_preset', self.app._generation_options()['options'])

    def test_unverified_drum_routing_does_not_claim_an_automatic_split(self):
        report = self.report(1)
        report['instruments'][0].update(name='DRUMS', plugin='Unison Drum Monkey',
                                        lanes=[], drum_routing={'status': 'unsupported',
                                        'reason': 'The saved kick has no separate output.'})
        self.load(report)
        self.assertIn('Automatic drum routing is unavailable', self.app.preview_notice.get())
        self.assertIn('The saved kick has no separate output.', self.app.preview_notice.get())
        self.assertNotIn('Automatically routes the drums', self.app.preview_notice.get())

    def test_intensity_endpoints_and_default_contract(self):
        self.load()
        self.assertEqual(self.app._generation_options()['options']['intensity'], 1.6)
        self.assertEqual(self.app._generation_options()['options']['movement'], 'song')
        for intensity in (0, 100, 200):
            self.app.intensity.set(intensity)
            options = self.app._generation_options()['options']
            self.assertEqual(options['intensity'], intensity / 100)
            self.assertEqual(options['strength'], 1.0)
            self.assertEqual(options['expected_source_sha256'], digest(self.source))
            self.assertNotIn('expected_midi_sha256', options)
            self.assertNotIn('expected_template_sha256', options)
            self.assertEqual(options['fx_controls'], gui.DEFAULT_FX_CONTROLS)

    def test_fx_preset_buttons_set_controls_without_changing_song_or_other_settings(self):
        self.load()
        self.app.intensity.set(83)
        self.app.drop_fx.set('Let FX through')
        self.app.energy_painter.begin_stroke(0, .2)
        self.app.energy_painter.continue_stroke(384, .8)
        self.app.energy_painter.end_stroke()
        painting = self.app.energy_painter.snapshot_for(self.source)
        for label, document in gui.FX_PRESETS.items():
            with self.subTest(preset=label):
                self.app.fx_preset_buttons[label].invoke()
                request = self.app._generation_options()
                self.assertEqual(request['options']['fx_controls'], document)
                self.assertEqual(request['options']['intensity'], .83)
                self.assertEqual(request['options']['drop_fx'], 'keep')
                self.assertEqual(request['options']['energy_curve'], painting)
                self.assertEqual(request['source'], self.source)
                self.assertEqual(self.app.fx_preset_label.get(), label)
        frozen = self.app._generation_options()
        self.app.apply_fx_preset('Gentle')
        self.assertEqual(frozen['options']['fx_controls'], gui.FX_PRESETS['Huge throws'])

    def test_fx_numeric_percent_timing_and_slider_values_are_exported_exactly(self):
        self.load()
        self.app.fx_vars['delay']['amount'].set('42.5')
        self.app.fx_vars['delay']['peak'].set('99')
        self.app.fx_vars['delay']['rise_beats'].set('0.5')
        self.app.fx_vars['delay']['fade_bars'].set('16')
        self.app.fx_vars['delay']['throws'].set('Phrase ends')
        self.app.fx_duck.set('37')
        self.app.fx_bass_full_range.set(True)
        controls = self.app._generation_options()['options']['fx_controls']
        self.assertEqual(controls['delay'], {'amount': .425, 'peak': .99, 'rise_beats': .5,
                                          'fade_bars': 16, 'throws': 'phrases'})
        self.assertEqual(controls['duck'], .37)
        self.assertTrue(controls['bass_full_range'])
        self.assertEqual(self.app.fx_percent_controls[('delay', 'amount')]['value'].get(), 42.5)
        self.assertEqual(self.app.fx_preset_label.get(), 'Custom')
        request = self.app._generation_options()
        self.app._save_preferences(request)
        saved = json.loads(self.settings.read_text())
        self.assertEqual(saved['options']['fx_controls'], controls)
        self.app.destroy(); self.app = gui.FLPConnectorApp(); self.app.withdraw()
        self.assertEqual(self.app._fx_controls_snapshot(), controls)
        self.assertEqual(self.app.flp_path.get(), '')

    def test_fx_invalid_values_block_generation_instead_of_silent_clamping(self):
        self.load()
        for field, bad in (('amount', 'nan'), ('amount', '101'), ('amount', '-1'),
                           ('peak', 'infinity'), ('peak', 'bad'), ('peak', '10'),
                           ('rise_beats', '3'), ('fade_bars', '3'), ('throws', 'Random')):
            with self.subTest(field=field, value=bad):
                self.app.apply_fx_preset('Big & smooth')
                self.app.fx_vars['delay'][field].set(bad)
                with self.assertRaises(ValueError):
                    self.app._generation_options()
                self.assertEqual(self.app.fx_preset_label.get(), 'Check values')
        self.app.apply_fx_preset('Big & smooth')
        self.app.fx_duck.set('101')
        with self.assertRaises(ValueError):
            self.app._generation_options()

    def test_pump_depth_is_editable_only_for_balanced_mode_and_keeps_its_value(self):
        self.load()
        controls = self.app.fx_percent_controls[('shared', 'duck')]
        self.app.fx_duck.set('38')
        for mode in ('Dry', 'Let FX through'):
            self.app.drop_fx.set(mode)
            self.assertTrue(controls['slider'].instate(['disabled']))
            self.assertTrue(controls['number'].instate(['disabled']))
            self.assertEqual(self.app.fx_duck.get(), '38')
        self.app.drop_fx.set('Balanced')
        self.assertFalse(controls['number'].instate(['disabled']))
        self.assertEqual(self.app._generation_options()['options']['fx_controls']['duck'], .38)

    def test_options_validation_and_frozen_snapshot(self):
        self.load()
        request = self.app._generation_options()
        frozen = copy.deepcopy(request)
        self.app.intensity.set(200); self.app.sylenth_movement.set(False); self.app.seed.set(8)
        self.app.drop_bars.set('17, 49'); self.app.fx_style.set('Fuller tails')
        self.assertEqual(request, frozen)
        options = self.app._generation_options()['options']
        self.assertEqual(options['drop_bars'], '17,49')
        self.assertEqual(options['fx_style'], 'fuller')
        for bad in ('17.5', '0', '65', 'abc'):
            self.app.drop_bars.set(bad)
            with self.assertRaises(ValueError): self.app._generation_options()
        self.app.drop_bars.set('')
        for bad in (-1, 201, float('inf')):
            self.app.intensity.set(bad)
            with self.assertRaises(ValueError): self.app._generation_options()

    def test_source_change_invalidates_preview_and_stale_result_is_ignored(self):
        self.load(); report = self.report(); token = self.app._inspect_token
        self.app.flp_path.set(str(self.folder / 'Other.flp'))
        self.assertIsNone(self.app.project_info)
        self.assertTrue(self.app.generate_button.instate(['disabled']))
        self.app._results.put(('inspect', token, self.source, gui.file_signature(self.source), report, None))
        self.app._poll_results()
        self.assertIsNone(self.app.project_info)
        self.assertEqual(len(self.app.instrument_table.get_children()), 0)

    def test_same_size_timestamp_source_change_is_rejected(self):
        self.load(); old_stat = self.source.stat()
        content = bytearray(self.source.read_bytes()); content[-1] ^= 1; self.source.write_bytes(content)
        os.utime(self.source, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'changed after the preview'):
            self.app._generation_options()
        self.assertTrue(self.app.generate_button.instate(['disabled']))

    def test_inspection_worker_checks_hash_and_hands_report_to_preview(self):
        self.load(); self.app.project_info = None; self.app.loading = True
        report = self.report(); token = self.app._inspect_token
        module = types.SimpleNamespace(inspect_flp=lambda path: copy.deepcopy(report))
        with patch.dict(sys.modules, {'flp_connector': module}), patch.object(gui, 'inspect_flp_timeline', return_value=report['energy_overview']):
            self.app._inspect_worker(token, self.source, gui.file_signature(self.source))
        self.app._poll_results()
        self.assertEqual(self.app.project_info, report)
        self.assertFalse(self.app.generate_button.instate(['disabled']))
        module.inspect_flp = lambda path: {**report, 'sha256': '0' * 64}
        with patch.dict(sys.modules, {'flp_connector': module}):
            self.app._inspect_worker(token, self.source, gui.file_signature(self.source))
        self.app._poll_results()
        self.assertIsNone(self.app.project_info)
        self.assertIn('changed while', self.app.preview_notice.get())

    def test_invalid_source_is_blocked_without_backend_call(self):
        self.app.flp_path.set(str(self.folder / 'Wrong.mid'))
        with patch.object(gui.threading, 'Thread') as thread:
            self.app.inspect_source()
        thread.assert_not_called()
        self.assertIn('existing FL Studio', self.app.preview_notice.get())
        self.assertTrue(self.app.generate_button.instate(['disabled']))

    def test_backend_unsupported_reason_without_hash_reaches_user(self):
        self.load(); self.app.loading = True
        report = {'supported': False, 'errors': ['Twenty-one musical instruments exceed the supported maximum.'],
                  'warnings': [], 'instruments': [], 'path': str(self.source)}
        module = types.SimpleNamespace(inspect_flp=lambda path: report)
        with patch.dict(sys.modules, {'flp_connector': module}):
            self.app._inspect_worker(self.app._inspect_token, self.source, gui.file_signature(self.source))
        self.app._poll_results()
        self.assertIn('Twenty-one', self.app.preview_notice.get())
        self.assertNotIn('changed while', self.app.preview_notice.get())
        self.assertTrue(self.app.generate_button.instate(['disabled']))

    def test_generate_freezes_worker_saves_new_copy_and_preserves_legacy_preferences(self):
        self.load(); source_hash = digest(self.source)
        captured = {}
        class PendingThread:
            def __init__(_self, target, args, daemon): captured.update(target=target, args=args)
            def start(_self): pass
        with patch.object(gui.threading, 'Thread', PendingThread): self.app.generate()
        self.assertTrue(self.app.running)
        self.assertTrue(self.app.generate_button.instate(['disabled']))
        request, destination = captured['args']
        self.assertNotEqual(destination, self.source)
        self.assertEqual(destination.parent.parent, self.folder / 'exports')
        self.assertTrue(destination.parent.is_dir())
        saved = json.loads(self.settings.read_text())
        self.assertEqual(self.legacy_settings.read_bytes(), self.before_settings)
        self.assertEqual(set(saved), {'version', 'defaults_revision', 'source_folder', 'output_folder', 'options'})
        self.assertEqual(saved['defaults_revision'], gui.DEFAULTS_REVISION)
        self.assertEqual(saved['source_folder'], str(self.source.parent.resolve()))
        self.assertNotIn(self.source.name, self.settings.read_text())
        self.assertEqual(saved['options']['intensity'], 1.6)
        self.assertNotIn('drop_bars', saved['options'])
        self.app.intensity.set(0); self.app.flp_path.set('A different song.flp')
        def export(source, target, options):
            self.assertEqual(source, self.source)
            self.assertEqual(options['intensity'], 1.6)
            self.assertEqual(options['expected_source_sha256'], source_hash)
            target.write_bytes(b'generated fixture')
            return {'source_flp': str(source), 'source_sha256': source_hash,
                    'output_flp': str(target), 'output_sha256': digest(target), 'instrument_count': 3,
                    'automation_tracks': [1, 2], 'warnings': ['Explicit coverage notice.']}
        with patch.dict(sys.modules, {'flp_connector': types.SimpleNamespace(export_flp=export)}):
            captured['target'](*captured['args'])
        self.app._poll_results()
        self.assertEqual(digest(self.source), source_hash)
        self.assertIsNone(self.app.last_successful_flp)
        self.assertIn('Earlier selection saved', self.app.status.get())
        self.assertIn(str(self.source), self.app.log.get('1.0', 'end'))
        self.assertIn(str(destination), self.app.log.get('1.0', 'end'))
        self.assertTrue(self.app.open_flp_button.instate(['disabled']))
        self.infos.assert_not_called()

    def test_remembered_flp_is_separate_and_not_written_on_startup(self):
        data = {'defaults_revision': gui.DEFAULTS_REVISION,
                'source_folder': str(self.source.parent), 'output_folder': str(self.folder / 'remembered'),
                'options': {'intensity': 2, 'sylenth_movement': False, 'movement': 'groove'}}
        self.settings.write_text(json.dumps(data)); before = self.settings.read_bytes()
        self.app.destroy(); self.app = gui.FLPConnectorApp(); self.app.withdraw()
        self.assertEqual(self.app.flp_path.get(), '')
        self.assertEqual(self.app.source_folder, str(self.source.parent))
        self.assertFalse(self.app.loading)
        self.assertEqual(self.app.intensity.get(), 200)
        # This connector now always uses the user's straight-ramp mode;
        # migrating an older style must retain intensity and other choices.
        self.assertEqual(self.app.movement.get(), 'Full song — straight ramps')
        self.assertFalse(self.app.sylenth_movement.get())
        self.assertEqual(self.settings.read_bytes(), before)

    def test_painter_draws_one_exact_straight_segment_without_mouse_jitter(self):
        self.load()
        painter = self.app.energy_painter
        self.assertFalse(painter.enabled.get())
        self.assertIsNone(self.app._generation_options()['options']['energy_curve'])
        self.assertNotIn('Smooth', [button.cget('text') for button in painter._action_buttons])
        painter.begin_stroke(17, .2)
        painter.continue_stroke(110, .95)
        painter.continue_stroke(450, .07)
        painter.continue_stroke(997, .8)
        painter.end_stroke()
        curve = painter.snapshot_for(self.source, digest(self.source))
        self.assertEqual([p for p in curve['points'] if 17 <= p[0] <= 997], [[17, .2], [997, .8]])
        self.assertEqual(curve['points'][0], [0, .5])
        self.assertEqual(curve['points'][-1], [64 * 384, .5])
        self.assertEqual(curve['source_sha256'], digest(self.source))
        from energy_curve import sample_energy_curve
        self.assertAlmostEqual(sample_energy_curve(curve, [507])[0], .5)
        painter.undo_curve()
        self.assertFalse(painter.enabled.get())
        self.assertTrue(all(value == .5 for _, value in painter.state.document['points']))

    def test_painting_snapshot_is_frozen_not_saved_as_global_preference(self):
        self.load()
        painter = self.app.energy_painter
        painter.begin_stroke(0, .1); painter.continue_stroke(64 * 384, .9); painter.end_stroke()
        request = self.app._generation_options()
        expected = copy.deepcopy(request['options']['energy_curve'])
        self.app._save_preferences(request)
        self.assertNotIn('energy_curve', json.loads(self.settings.read_text())['options'])
        painter.reset_curve()
        self.assertEqual(request['options']['energy_curve'], expected)
        self.assertEqual(expected['points'], [[0, .1], [64 * 384, .9]])

    def test_painting_never_leaks_to_a_changed_or_reselected_song(self):
        self.load()
        painter = self.app.energy_painter
        painter.begin_stroke(0, .2); painter.continue_stroke(384, .8); painter.end_stroke()
        old = painter.snapshot_for(self.source)
        stat = self.source.stat()
        content = bytearray(self.source.read_bytes()); content[-1] ^= 1; self.source.write_bytes(content)
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'FLP changed'):
            painter.snapshot_for(self.source)
        self.load()
        self.assertFalse(painter.enabled.get())
        self.assertNotEqual(painter.state.overview['source_sha256'], old['source_sha256'])
        self.assertTrue(all(v == .5 for _, v in painter.state.document['points']))
        self.app.flp_path.set(str(self.folder / 'Different.flp'))
        self.assertIsNone(painter.state)
        self.assertFalse(painter.enabled.get())
        self.assertEqual(painter._cache, {})

    def test_same_song_recheck_keeps_paint_but_changed_identity_resets_it(self):
        self.load()
        painter = self.app.energy_painter
        painter.begin_stroke(5, .25); painter.continue_stroke(509, .75); painter.end_stroke()
        curve = painter.snapshot_for(self.source)
        painter.begin_project_check()
        self.app._show_preview(self.report())
        self.assertEqual(painter.snapshot_for(self.source), curve)
        report = self.report()
        report['energy_overview']['source_sha256'] = '0' * 64
        self.app._show_preview(report)
        self.assertFalse(painter.enabled.get())
        self.assertTrue(all(v == .5 for _, v in painter.state.document['points']))

    def test_linear_success_log_describes_ramps_and_painted_energy(self):
        self.load()
        destination = self.folder / 'Straight result.flp'
        destination.write_bytes(b'fixture result')
        context = {'source': str(self.source), 'selection_token': self.app._selection_token,
                   'sha256': digest(self.source)}
        report = {'instrument_count': 3, 'bars': 64, 'automation_tracks': [{}, {}, {}],
                  'song_movement': {'curve_shape': 'piecewise_linear', 'builds': [], 'gestures': []},
                  'options': {'energy_curve': self.app.energy_painter.state.document}, 'warnings': []}
        self.app.running = True
        self.app._results.put(('export', destination, report, context, None))
        self.app._poll_results()
        log = self.app.log.get('1.0', 'end')
        self.assertIn('straight ramps and holds across 64 bars for 3 controls', log)
        self.assertIn('Painted energy included.', log)
        self.assertNotIn('0 builds', log)
        self.assertNotIn('0 featured FX throws', log)

    def test_export_failure_clears_previous_success_target(self):
        self.load(); previous = self.folder / 'Previous.flp'
        self.app.last_successful_flp = previous
        self.app.last_successful_output = self.folder
        self.app.running = True
        context = {'source': str(self.source), 'selection_token': self.app._selection_token, 'sha256': digest(self.source)}
        self.app._results.put(('export', self.folder / 'Failed.flp', None, context, 'Expected failure'))
        self.app._poll_results()
        self.assertIsNone(self.app.last_successful_flp)
        self.assertIsNone(self.app.last_successful_output)
        self.assertTrue(self.app.open_flp_button.instate(['disabled']))
        self.assertTrue(self.app.open_button.instate(['disabled']))
        self.assertFalse(self.app.running)
        self.assertFalse(self.app.generate_button.instate(['disabled']))
        self.errors.assert_called_once()

    def test_worker_rejects_false_success_without_output(self):
        self.load(); request = self.app._generation_options()
        module = types.SimpleNamespace(export_flp=lambda *args, **kwargs: {'output_flp': 'absent.flp'})
        with patch.dict(sys.modules, {'flp_connector': module}):
            self.app._export_worker(request, self.folder / 'Missing.flp')
        result = self.app._results.get_nowait()
        self.assertIn('did not create', result[-1])

    def test_worker_rejects_wrong_source_or_output_provenance(self):
        self.load(); request = self.app._generation_options()
        for field in ('source_flp', 'source_sha256', 'output_flp', 'output_sha256'):
            with self.subTest(field=field):
                destination = self.folder / (field + '.flp')
                def export(source, target, options):
                    target.write_bytes(b'fixture result')
                    report = {'source_flp': str(source), 'source_sha256': digest(source),
                              'output_flp': str(target), 'output_sha256': digest(target)}
                    report[field] = str(self.folder / 'wrong.flp') if field.endswith('flp') else '0' * 64
                    return report
                with patch.dict(sys.modules, {'flp_connector': types.SimpleNamespace(export_flp=export)}):
                    self.app._export_worker(request, destination)
                result = self.app._results.get_nowait()
                self.assertIn('does not match', result[-1])
                self.app._results.put(result); self.app._poll_results()
                self.assertIsNone(self.app.last_successful_flp)
                self.assertTrue(self.app.open_flp_button.instate(['disabled']))

    def test_source_selection_and_new_export_clear_old_open_buttons(self):
        self.load()
        def prior_result():
            self.app.last_successful_flp = self.folder / 'Old output.flp'
            self.app.last_successful_output = self.folder
            self.app.open_flp_button.configure(state='normal'); self.app.open_button.configure(state='normal')
        prior_result()
        self.app.flp_path.set(str(self.folder / 'Another source.flp'))
        self.assertIsNone(self.app.last_successful_flp)
        self.assertTrue(self.app.open_flp_button.instate(['disabled']))
        self.assertTrue(self.app.open_button.instate(['disabled']))
        self.load(); prior_result()
        with patch.object(gui.threading, 'Thread'):
            self.app.inspect_source()
        self.assertIsNone(self.app.last_successful_flp)
        self.assertTrue(self.app.open_flp_button.instate(['disabled']))
        self.load(); prior_result()
        self.app.intensity.set(300); self.app.generate()
        self.assertIsNone(self.app.last_successful_flp)
        self.assertTrue(self.app.open_flp_button.instate(['disabled']))
        self.app.intensity.set(140); prior_result()
        with patch.object(gui.threading, 'Thread') as thread:
            self.app.generate()
        thread.return_value.start.assert_called_once()
        self.assertIsNone(self.app.last_successful_flp)
        self.assertTrue(self.app.open_flp_button.instate(['disabled']))
        self.assertTrue(self.app.open_button.instate(['disabled']))

    def test_new_run_folders_never_reuse_existing_directory(self):
        base = self.folder / 'new runs'
        first = gui.new_run_folder(base); second = gui.new_run_folder(base)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir() and second.is_dir())

    def test_close_waits_for_active_export(self):
        self.app.running = True
        self.app.close()
        self.assertFalse(self.app._closed)
        self.assertIn('still being saved', self.app.status.get())

    def test_minimum_window_keeps_actions_and_controls_within_width(self):
        self.load(self.report(20))
        self.app.attributes('-alpha', 0)
        self.app.geometry('740x620'); self.app.deiconify(); self.app.update()
        right = self.app.winfo_rootx() + self.app.winfo_width()
        overflow = []
        def visit(widget):
            for child in widget.winfo_children():
                if child.winfo_ismapped() and child.winfo_rootx() + child.winfo_width() > right + 1:
                    overflow.append((child.winfo_class(), child.winfo_rootx() + child.winfo_width() - right))
                visit(child)
        visit(self.app)
        self.assertEqual(overflow, [])
        self.assertLess(self.app.generate_button.winfo_rooty() + self.app.generate_button.winfo_height(),
                        self.app.winfo_rooty() + self.app.winfo_height())

if __name__ == "__main__": unittest.main(verbosity=2)

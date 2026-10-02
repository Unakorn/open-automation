"""Portable behavior checks for explicit Delay & Reverb controls."""
import copy
import json
import unittest
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'work'))
from helpers import *
import test_gentle_fx as gentle_legacy
def expected_default():
    return {"delay": {"amount": .25, "peak": .85, "rise_beats": 4, "fade_bars": 4, "throws": "every8"},
            "reverb": {"amount": .30, "peak": .90, "rise_beats": 4, "fade_bars": 8, "throws": "every8"},
            "duck": .15, "bass_full_range": False}


def settings(lane=None, **changes):
    document = expected_default()
    if lane:
        document[lane].update(changes)
    else:
        document.update(changes)
    return document


def peaks(anchors, level):
    return [tick for i, (tick, value) in enumerate(anchors)
            if abs(value - level) < 1e-10 and (not i or anchors[i - 1][1] < level - 1e-10)]


class FxControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import linear_control_midi
        import fx_controls
        cls.engine, cls.controls = linear_control_midi, fx_controls
        cls.base = dict(gentle_legacy.GentleFxTests.options, drop_fx="keep")

    def fx(self, lane, document=None, notes=None, song=None, role="lead", **overrides):
        options = dict(self.base, fx_controls=document if document is not None else expected_default(), **overrides)
        return self.engine.fx_anchors(song or plan(64), notes if notes is not None else [held(1, 8)],
                                      role, lane, options, voice_ids=[2])

    def test_default_preset_and_validation_return_independent_copies(self):
        self.assertEqual(self.controls.DEFAULT_FX_CONTROLS, expected_default())
        self.assertTrue(any(value == expected_default() for value in self.controls.FX_PRESETS.values()))
        doc = expected_default()
        clean = self.controls.validate_fx_controls(doc)
        self.assertEqual(clean, doc)
        clean["delay"]["amount"] = 0
        self.assertEqual(doc, expected_default())
        self.assertEqual(self.controls.DEFAULT_FX_CONTROLS, expected_default())
        for preset in self.controls.FX_PRESETS.values():
            self.assertEqual(self.controls.validate_fx_controls(preset), preset)

    def test_strict_schema_rejects_nonfinite_bool_unknown_and_out_of_range_values(self):
        bad = [None, [], {"delay": {}}, dict(expected_default(), extra=True)]
        for key, value in (("amount", True), ("amount", -.1), ("peak", 1.1), ("peak", float("nan")),
                           ("rise_beats", True), ("rise_beats", 3), ("fade_bars", 3), ("throws", "random")):
            bad.append(settings("delay", **{key: value}))
        bad.extend([settings(duck=float("inf")), settings(duck=True), settings(bass_full_range=1),
                    settings("delay", amount=.8, peak=.7)])
        missing = expected_default(); missing["reverb"].pop("amount"); bad.append(missing)
        extra = expected_default(); extra["reverb"]["unused"] = .5; bad.append(extra)
        for value in bad:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                self.controls.validate_fx_controls(value)

    def test_amount_controls_the_played_bed_and_tracks_own_velocity(self):
        notes = [held(1, 24, 110)]
        for lane in ("delay", "reverb"):
            low = self.fx(lane, settings(lane, amount=.10, peak=.80, throws="off"), notes)
            high = self.fx(lane, settings(lane, amount=.50, peak=.80, throws="off"), notes)
            self.assertGreater(interpolate(high, 8 * BAR), interpolate(low, 8 * BAR) + .10)
            quiet = self.fx(lane, settings(lane, throws="off"), [held(1, 24, 25)])
            loud = self.fx(lane, settings(lane, throws="off"), [held(1, 24, 120)])
            self.assertGreater(interpolate(loud, 8 * BAR), interpolate(quiet, 8 * BAR) + .01)

    def test_peak_is_absolute_and_independent_of_nonzero_intensity_and_legacy_style(self):
        for lane in ("delay", "reverb"):
            doc = settings(lane, amount=.2, peak=.93, throws="phrases")
            curves = [self.fx(lane, doc, intensity=intensity, fx_style=style)
                      for intensity in (.1, 1, 2) for style in ("original", "fuller", "full_range")]
            for anchors in curves:
                self.assertAlmostEqual(max(v for _, v in anchors), .93, places=12)
            self.assertTrue(all(curve == curves[0] for curve in curves[1:]))
            zero = self.fx(lane, doc, intensity=0)
            self.assertTrue(all(v == FLOOR for _, v in zero))
            lower = self.fx(lane, settings(lane, amount=.2, peak=.4, throws="phrases"))
            self.assertAlmostEqual(max(v for _, v in lower), .4, places=12)

    def test_rise_controls_a_straight_ramp_without_changing_the_selected_peak(self):
        for lane in ("delay", "reverb"):
            first_peaks = []
            for beats in (.5, 1, 2, 4, 8):
                anchors = self.fx(lane, settings(lane, amount=0, peak=.8, rise_beats=beats, throws="phrases"))
                peak = peaks(anchors, .8)[0]
                first_peaks.append(peak)
                start = peak - round(beats * PPQ)
                self.assertAlmostEqual(interpolate(anchors, start), FLOOR)
                self.assertAlmostEqual(interpolate(anchors, (start + peak) / 2), (FLOOR + .8) / 2, places=12)
            self.assertEqual(len(set(first_peaks)), 1)

    def test_fade_control_changes_only_tail_duration_and_supports_all_visible_choices(self):
        phrase_end = 9 * BAR
        for lane in ("delay", "reverb"):
            for bars in (1, 2, 4, 8, 16):
                anchors = self.fx(lane, settings(lane, amount=0, peak=.8, fade_bars=bars, throws="phrases"))
                finish = phrase_end + bars * BAR
                self.assertAlmostEqual(interpolate(anchors, phrase_end), .8)
                self.assertAlmostEqual(interpolate(anchors, phrase_end + bars * BAR / 2), (.8 + FLOOR) / 2, places=12)
                self.assertAlmostEqual(interpolate(anchors, finish), FLOOR)

    def test_throws_off_preserves_bed_but_zero_amount_and_peak_disable_even_painting(self):
        song = plan(64)
        paint = curve_document([[0, 1], [song["end_tick"], 1]], end=song["end_tick"])
        for lane in ("delay", "reverb"):
            off = self.fx(lane, settings(lane, amount=.2, peak=1, throws="off"))
            on = self.fx(lane, settings(lane, amount=.2, peak=1, throws="phrases"))
            self.assertGreater(max(v for _, v in off), FLOOR)
            self.assertLessEqual(max(v for _, v in off), .2)
            self.assertEqual(max(v for _, v in on), 1)
            disabled = self.fx(lane, settings(lane, amount=0, peak=0), energy_curve=paint)
            self.assertTrue(all(v == FLOOR for _, v in disabled))

    def test_periodic_throw_cadence_is_distinct_and_never_invents_empty_part_peaks(self):
        active = [NoteEvent(t, 3 * PPQ // 4, 64, 100, 0) for t in range(0, 24 * BAR, PPQ)]
        for lane in ("delay", "reverb"):
            four = self.fx(lane, settings(lane, amount=.1, peak=.9, fade_bars=1, throws="every4"), active)
            eight = self.fx(lane, settings(lane, amount=.1, peak=.9, fade_bars=1, throws="every8"), active)
            self.assertGreaterEqual(len(peaks(four, .9)), 5)
            self.assertGreaterEqual(len(peaks(eight, .9)), 2)
            self.assertGreater(len(peaks(four, .9)), len(peaks(eight, .9)))
            empty = self.fx(lane, settings(lane, amount=.5, peak=1, throws="every4"), [])
            self.assertTrue(all(v == FLOOR for _, v in empty))
            gapped = [n for n in active if n.tick < 8 * BAR] + [
                NoteEvent(t, 3 * PPQ // 4, 64, 100, 0) for t in range(24 * BAR, 32 * BAR, PPQ)]
            curve = self.fx(lane, settings(lane, amount=.1, peak=1, fade_bars=4, throws="every4"), gapped)
            self.assertTrue(all(interpolate(curve, tick) == FLOOR for tick in range(14 * BAR, 23 * BAR, PPQ)))

    def test_staccato_periodic_throw_keeps_selected_rise_while_catching_a_real_note(self):
        notes = [NoteEvent(t, PPQ // 16, 64, 100, 0) for t in range(0, 24 * BAR, PPQ)]
        for lane in ("delay", "reverb"):
            anchors = self.fx(lane, settings(lane, amount=0, peak=.8, rise_beats=4, throws="every8"), notes)
            peak = peaks(anchors, .8)[0]
            self.assertTrue(any(n.tick <= peak < n.tick + n.duration for n in notes))
            start = peak - 4 * PPQ
            self.assertAlmostEqual(interpolate(anchors, start), FLOOR)
            self.assertAlmostEqual(interpolate(anchors, peak - 2 * PPQ), (FLOOR + .8) / 2, places=12)
            self.assertLess(interpolate(anchors, peak) - interpolate(anchors, peak - 1), .01,
                            "A short final note collapsed the requested four-beat rise")

    def test_full_wet_long_tail_and_light_duck_work_together(self):
        song = plan(80, [(11, 19)])
        for lane in ("delay", "reverb"):
            doc = settings(lane, amount=.25, peak=1, rise_beats=4, fade_bars=16, throws="phrases")
            doc["duck"] = .15
            normal = self.fx(lane, doc, song=song, drop_fx="keep")
            ducked = self.fx(lane, doc, song=song, drop_fx="reduced")
            self.assertEqual(max(v for _, v in ducked), 1)
            self.assertGreater(interpolate(ducked, 24 * BAR), FLOOR)
            self.assertAlmostEqual(interpolate(ducked, 25 * BAR), FLOOR)
            self.assertAlmostEqual(interpolate(ducked, 11 * BAR) - FLOOR,
                                   .85 * (interpolate(normal, 11 * BAR) - FLOOR), places=12)
            self.assertGreater(interpolate(ducked, 11 * BAR), .5)

    def test_duck_only_changes_balanced_and_has_a_continuous_depth_control(self):
        song, notes = plan(64, [(8, 16)]), [held(1, 24)]
        for lane in ("delay", "reverb"):
            zero, deep = settings(lane, amount=.5, peak=.9, throws="off"), settings(lane, amount=.5, peak=.9, throws="off")
            zero["duck"], deep["duck"] = 0, .7
            normal = self.fx(lane, zero, notes, song, drop_fx="reduced")
            lowered = self.fx(lane, deep, notes, song, drop_fx="reduced")
            self.assertAlmostEqual(interpolate(lowered, 8 * BAR) - FLOOR,
                                   .3 * (interpolate(normal, 8 * BAR) - FLOOR), places=12)
            self.assertEqual(self.fx(lane, zero, notes, song, drop_fx="keep"),
                             self.fx(lane, deep, notes, song, drop_fx="keep"))

    def test_delay_and_reverb_settings_are_independent(self):
        for changed, unchanged in (("delay", "reverb"), ("reverb", "delay")):
            doc = settings(changed, amount=.05, peak=1, rise_beats=.5, fade_bars=16, throws="every4")
            self.assertEqual(self.fx(unchanged), self.fx(unchanged, doc))
            self.assertNotEqual(self.fx(changed), self.fx(changed, doc))

    def test_bass_protection_requires_explicit_override(self):
        for lane in ("delay", "reverb"):
            doc = settings(lane, amount=.5, peak=1, throws="phrases")
            limited = self.fx(lane, doc, role="bass")
            self.assertLessEqual(max(v for _, v in limited), .1)
            doc["bass_full_range"] = True
            enabled = self.fx(lane, doc, role="bass")
            self.assertEqual(max(v for _, v in enabled), 1)

    def test_internal_wet_settings_preserve_saved_patch_bounds_and_nonwet_curves(self):
        song, notes = plan(64, [(8, 16)]), [held(1, 24)]
        for lane in ("delay", "reverb"):
            doc = settings(lane, amount=.7, peak=1, rise_beats=.5, fade_bars=16, throws="every4")
            doc["bass_full_range"] = True
            options = dict(self.base, fx_controls=doc)
            anchors = self.engine.synth_anchors(song, control(lane), notes, options)
            self.assertTrue(all(.05 <= v <= .8 for _, v in anchors))
            self.assertEqual(anchors[0], (0, .2)); self.assertEqual(anchors[-1], (song["end_tick"] - 1, .2))
            for char, parameter in (("brightness", 42), ("width", 15), ("effect", 16), ("effect", 36)):
                c = dict(control(lane), character=char, parameter_id=parameter)
                self.assertEqual(self.engine.synth_anchors(song, c, notes, self.base),
                                 self.engine.synth_anchors(song, c, notes, options))
            self.assertEqual(self.engine.fx_anchors(song, notes, "lead", "cutoff", self.base, [2]),
                             self.engine.fx_anchors(song, notes, "lead", "cutoff", options, [2]))

    def test_missing_controls_preserve_all_legacy_wet_anchor_bytes(self):
        golden = json.loads(Path(__file__).with_name("fx_controls_legacy_golden.json").read_text())["anchors"]
        song, notes = plan(drops=[(8, 16)]), [held(), held(6, 5, 70), held(20, 8)]
        actual = {}
        for policy in ("keep", "reduced", "dry"):
            options = dict(gentle_legacy.GentleFxTests.options, drop_fx=policy)
            for lane in ("delay", "reverb"):
                for role in ("lead", "bass", "drums"):
                    actual[f"{policy}:{role}:{lane}"] = self.engine.fx_anchors(song, notes, role, lane, options, voice_ids=[2])
                actual[f"{policy}:synth:{lane}"] = self.engine.synth_anchors(song, control(lane), notes, options)
        self.assertEqual(json.loads(json.dumps(actual)), golden)

if __name__ == "__main__": unittest.main()

"""Portable fade and bounds regressions."""
import json
import unittest
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'work'))
import linear_control_midi as engine
from full_song_movement import build_plan
from helpers import *
class GentleFxTests(unittest.TestCase):
    options = {"movement": "song", "intensity": 1.6, "strength": 1, "seed": 1,
               "drop_fx": "reduced", "fx_style": "full_range", "phrase_bars": 16,
               "drop_length": 8, "division": 16, "energy_curve": None}

    def anchors(self, song, notes, lane, options=None, internal=False):
        options = options or self.options
        return (engine.synth_anchors(song, control(lane), notes, options) if internal else
                engine.fx_anchors(song, notes, "lead", lane, options, voice_ids=[2]))

    def test_delay_and_reverb_finish_after_four_and_eight_bar_fades(self):
        song, note = plan(), held()
        end = note.tick + note.duration
        for lane, bars in (("delay", 4), ("reverb", 8)):
            for internal in (False, True):
                with self.subTest(lane=lane, internal=internal):
                    anchors = self.anchors(song, [note], lane, internal=internal)
                    baseline = .2 if internal else FLOOR
                    wet = interpolate(anchors, end)
                    self.assertGreater(wet, baseline + .03)
                    finish = end + bars * BAR
                    self.assertGreater(interpolate(anchors, finish - BAR // 2), baseline)
                    self.assertAlmostEqual(interpolate(anchors, finish), baseline)
                    middle = interpolate(anchors, end + bars * BAR // 2)
                    self.assertAlmostEqual(middle, (wet + baseline) / 2, places=10,
                                           msg="Nominal release is not one straight fade")

    def test_new_quiet_phrase_cannot_cut_off_the_previous_tail(self):
        song = plan()
        first, following = held(), held(first_bar=6, bars=2, velocity=30)
        for lane in ("delay", "reverb"):
            for internal in (False, True):
                with self.subTest(lane=lane, internal=internal):
                    alone = self.anchors(song, [first], lane, internal=internal)
                    combined = self.anchors(song, [first, following], lane, internal=internal)
                    for tick in range(following.tick - PPQ, following.tick + 2 * BAR, PPQ // 4):
                        self.assertGreaterEqual(interpolate(combined, tick) + 1e-12, interpolate(alone, tick),
                                                "Next onset shortened the existing release")
                    self.assertGreater(interpolate(combined, following.tick), .2 if internal else FLOOR)
                    self.assertLess(abs(interpolate(combined, following.tick) -
                                        interpolate(combined, following.tick - 1)), .01)

    def test_normal_throws_are_moderate_but_full_wet_remains_reachable(self):
        song, notes = plan(), [held()]
        paint = curve_document([[0, 1], [song["end_tick"], 1]], end=song["end_tick"])
        for lane in ("delay", "reverb"):
            normal = self.anchors(song, notes, lane)
            self.assertGreater(max(v for _, v in normal), .2)
            self.assertLess(max(v for _, v in normal), .75)
            strong = self.anchors(song, notes, lane, dict(self.options, intensity=2))
            painted = self.anchors(song, notes, lane, dict(self.options, energy_curve=paint))
            self.assertGreaterEqual(max(v for _, v in strong), .99)
            self.assertGreaterEqual(max(v for _, v in painted), .99)

    def test_short_parts_and_short_gaps_do_not_force_full_throws(self):
        song = plan()
        for lane in ("delay", "reverb"):
            long = self.anchors(song, [held()], lane)
            short = self.anchors(song, [held(bars=1)], lane)
            short_gap = [held(), NoteEvent(5 * BAR + PPQ * 2, 4 * BAR, 64, 100, 0)]
            combined = self.anchors(song, short_gap, lane)
            first_end = 5 * BAR
            self.assertLess(max(v for _, v in short), max(v for _, v in long))
            self.assertLess(interpolate(combined, first_end), interpolate(long, first_end))

    def test_bass_effect_limits_remain_protected_at_strong_settings(self):
        song, notes = plan(), [held()]
        paint = curve_document([[0, 1], [song["end_tick"], 1]], end=song["end_tick"])
        for lane in ("delay", "reverb"):
            for options in (self.options, dict(self.options, intensity=2),
                            dict(self.options, energy_curve=paint)):
                anchors = engine.fx_anchors(song, notes, "bass", lane, options, voice_ids=[2])
                self.assertLessEqual(max(v for _, v in anchors), .10)

    def test_balanced_dip_is_gentle_and_never_forces_a_sounding_tail_to_minimum(self):
        song = plan(drops=[(8, 16)])
        notes = [held(first_bar=0, bars=24)]
        for lane in ("delay", "reverb"):
            for internal in (False, True):
                with self.subTest(lane=lane, internal=internal):
                    normal = self.anchors(song, notes, lane, dict(self.options, drop_fx="keep"), internal)
                    balanced = self.anchors(song, notes, lane, internal=internal)
                    minimum = .05 if internal else FLOOR
                    for tick in range(8 * BAR - BAR, 8 * BAR + 2 * BAR + 1, PPQ // 4):
                        original, current = interpolate(normal, tick), interpolate(balanced, tick)
                        self.assertGreater(current, minimum + .01)
                        self.assertGreaterEqual(current - minimum, .65 * (original - minimum))
                    self.assertLess(abs(interpolate(balanced, 8 * BAR) -
                                        interpolate(balanced, 8 * BAR - 1)), .005)

    def test_explicit_dry_still_clears_the_entire_drop(self):
        song, notes = plan(drops=[(8, 16)]), [held(first_bar=0, bars=24)]
        for lane in ("delay", "reverb"):
            for internal in (False, True):
                anchors = self.anchors(song, notes, lane, dict(self.options, drop_fx="dry"), internal)
                minimum = .05 if internal else FLOOR
                for tick in (8 * BAR, 12 * BAR, 16 * BAR - 1):
                    self.assertAlmostEqual(interpolate(anchors, tick), minimum)

    def test_end_of_song_is_a_gradual_fade_not_a_last_tick_wet_spike(self):
        song, notes = plan(), [held(first_bar=0, bars=32)]
        paint = curve_document([[0, .8], [song["end_tick"], .8]], end=song["end_tick"])
        for lane, bars in (("delay", 4), ("reverb", 8)):
            for internal in (False, True):
                for options in (self.options, dict(self.options, energy_curve=paint)):
                    anchors = self.anchors(song, notes, lane, options, internal)
                    baseline = .2 if internal else FLOOR
                    last = song["end_tick"] - 1
                    self.assertEqual(anchors[-1], (last, baseline))
                    self.assertLess(abs(interpolate(anchors, last - 1) - baseline), .005)
                    values = [interpolate(anchors, tick) for tick in range(last - bars * BAR, last, PPQ)] + [baseline]
                    self.assertTrue(all(a + 1e-12 >= b for a, b in zip(values, values[1:])),
                                    "Final wet fade gained a new late peak")

    def test_all_fx_curves_keep_straight_segments_and_full_held_coverage(self):
        song = plan(drops=[(8, 16)])
        notes = [held(), held(first_bar=6, bars=5, velocity=70), held(first_bar=20, bars=8)]
        for lane in ("delay", "reverb"):
            for internal in (False, True):
                anchors = self.anchors(song, notes, lane, internal=internal)
                points = engine.sample_linear(anchors, PPQ, song["end_tick"], 16)
                self.assertEqual(points[0][0], 0)
                self.assertEqual(points[-1][0], song["end_tick"] - 1)
                self.assertEqual([t for t, _ in points], sorted({t for t, _ in points}))
                self.assertTrue({t for t, _ in anchors} <= {t for t, _ in points})
                duration = 0
                for index, (tick, value) in enumerate(points):
                    self.assertAlmostEqual(value, interpolate(anchors, tick), places=12)
                    following = points[index + 1][0] if index + 1 < len(points) else song["end_tick"]
                    self.assertGreater(following - tick, 0)
                    duration += following - tick
                self.assertEqual(duration, song["end_tick"])

    def test_cutoff_and_non_time_fx_synth_anchors_remain_byte_exact(self):
        golden = json.loads(Path(__file__).with_name("gentle_fx_unaffected_golden.json").read_text())["anchors"]
        model = synthetic_song()
        song = build_plan(model, self.options)
        notes = normalized_notes(model["voices"][1]["notes"])
        actual = {}
        for role in ("lead", "bass", "drums"):
            actual["fx_cutoff_" + role] = engine.fx_anchors(song, notes, role, "cutoff", self.options, voice_ids=[2])
        for character, parameter in (("brightness", 42), ("drive", 43), ("width", 15), ("modulation", 99),
                                     ("decay", 23), ("release", 24), ("effect", 16), ("effect", 36)):
            c = {"parameter_id": parameter, "channel_id": 2, "name": "Test", "role": "lead",
                 "character": character, "minimum": .2, "baseline": .45, "maximum": .8}
            actual["synth_" + str(parameter)] = engine.synth_anchors(song, c, notes, self.options)
        self.assertEqual(json.loads(json.dumps(actual)), golden)

if __name__ == "__main__": unittest.main()

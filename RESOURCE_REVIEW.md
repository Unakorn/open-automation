# Bundled resource review

The runtime has one bundled data file: `resources/automation-effects.json`.

Its version-1 structure contains a native Fruity Keyboard Controller definition and three effect definitions:

| Definition | Contents |
| --- | --- |
| Controller | Native channel defaults, envelopes and Keyboard Controller settings. The display name is the generic `Automation Control`; the exporter replaces it for each destination. |
| Cutoff | Fruity Filter identity, wrapper fields and numeric settings. |
| Delay | Fruity Delay 3 identity, wrapper fields and numeric settings. |
| Reverb | Fruity Wrapper fields, ValhallaFutureVerb VST3 identity, the standard Windows plugin location, and a `Default Preset` XML document containing effect parameters and interface dimensions. |

The catalog does not contain a plugin binary, executable, sample, recorded note sequence, musical FLP template or authorization file. The original source-project hash and donor channel label were removed for this publication. The remaining standard plugin path contains no user profile location.

Review included parsing every catalog hex event, examining ASCII and UTF-16 strings, looking for user paths and credential indicators, checking possible zlib streams, and examining the reverb XML fields. The XML contains preset/parameter/UI settings; no user name, email, license key, access token or registration field was found. Small binary fields are retained as native FL/plugin state and are not claimed to be fully documented format specifications.

The two JSON files under `tests/` are synthetic numerical curve expectations. They contain only label keys and pairs of numeric ticks/values. They are not user preferences or recorded music.

The native channel-default constant retained in `work/drum_monkey_template.py` contains numeric FL channel fields, not a Drum Monkey plugin preset or sample. The module is retained as a shared dependency; no saved Drum Monkey user state is distributed.

Excluded assets include the local drum-kit catalog, sample audio, instrument preset packs, all `.flp`/`.fst` files, personal configuration, generated MIDI, backups, Python runtimes and compiled files. Resource checks in `tests/test_package.py` guard this boundary.

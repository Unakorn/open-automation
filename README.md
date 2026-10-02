# Open Automation — FLP Automation Connector 2.5.0

A Python desktop script that reads your saved FL Studio song and creates a new copy with connected filter, delay, reverb and compatible Sylenth1 automation. Your FLP supplies the instruments, presets, notes and arrangement. No AI service or API key is required.

## What it does

- Reads up to 20 supported musical instrument channels and shows automation coverage before export.
- Builds straight ramps and holds from each part's notes, timing, velocity and rests.
- Provides **Gentle**, **Big & smooth** and **Huge throws** presets, plus separate delay/reverb amount, peak, rise, fade and timing controls.
- Keeps bass/sub effects limited to 10% unless you explicitly enable their full range.
- Lets you paint an overall energy curve using straight segments. The curve is tied to the selected FLP, so changing songs clears it.
- Adds combined automation patterns in eight-bar sections on one Playlist lane, with companion control MIDI and an export report.
- Preserves the original file and refuses to overwrite an existing output. Intensity 0 creates an unchanged copy.

## Requirements

- Windows and Python **3.11 or newer**, including **Tcl/Tk and IDLE** in the Python installer. The source package was checked with Python 3.13.
- `mido`, installed using the commands below.
- FL Studio and the plugins used in your own project, installed and available to FL Studio.
- The added effects use **Fruity Filter**, **Fruity Delay 3**, **Fruity Keyboard Controller** and **ValhallaFutureVerb VST3**. The bundled definition uses the standard Windows path `C:\Program Files\Common Files\VST3\ValhallaFutureVerb.vst3`. The plugin itself and any required license are not included.
- Sylenth1 is optional. Its internal controls are added only when its saved state matches the supported format. The inspected format is Sylenth1 3.073's bank version 3045.
- Drum Monkey is optional. The connector handles only verified saved output layouts; check the coverage notice for whether drum routing or effects were skipped.

This is an experimental FLP reader/writer for supported layouts, not an official Image-Line tool. Unknown layouts, existing controller links, shared routes and occupied effect slots can limit or prevent automation. Full native playback compatibility with every FL Studio version or plugin state is not claimed.

## Install and run

Download or clone the repository into a writable folder. Open PowerShell there:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe "outputs\FLP Automation Connector.py"
```

After installation, double-click **Open Automation Project Builder.bat**. It uses `.venv` when present, otherwise `python` on your PATH. This is a script distribution; no executable or bundled Python runtime is supplied.

1. Save your song in FL Studio and select that `.flp` under **Your saved song**.
2. Read the instrument coverage and warnings.
3. Choose a Delay & Reverb preset or edit its controls. **Normal amount** sets the underlying level; **throws** are temporary FX boosts. **Balanced** uses the selected pump depth at drops, **Dry** reduces added FX during drops, and **Let FX through** removes that dip.
4. Adjust other movement or paint the overall energy if wanted. Changes take effect when generating a new copy; this is not a live audio plugin.
5. Click **Create automated copy**, then open the generated FLP in FL Studio and audition it.

Delay and reverb controls are independent of the general Intensity setting except at Intensity 0. Compatible internal Sylenth controls remain inside their saved preset ranges. MIDI control values are quantized, so some displayed percentages have a small rounding difference.

Preferences are saved locally in `flp_connector_settings.json`. Default exports go under `outputs/projects/`. Neither preferences nor generated projects belong in source control; `.gitignore` excludes them. Your own samples and plugin installations must remain available for your FLP to play.

## Tests

Run the portable synthetic-data and preference checks:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Optional hidden Tk interface checks require a desktop session:

```powershell
$env:OPEN_AUTOMATION_GUI_TESTS = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Remove-Item Env:\OPEN_AUTOMATION_GUI_TESTS
```

Tests use synthetic note data and temporary files. They do not open or control FL Studio and do not prove that audio sounds good. Private-song integration fixtures are intentionally excluded.

## Source package scope

`outputs/` contains the GUI and energy painter source. `work/` contains the parser, export and movement modules. `resources/automation-effects.json` holds only the effect/controller settings needed to add automation. See [the resource review](RESOURCE_REVIEW.md) for its contents.

This release contains the current saved-FLP connector. The older MIDI/template-based builder, personal templates, preset packs, drum sample kit, local settings, generated songs and backups are excluded. Shared helper modules retain some older routines, but the supported entry point is the connector launcher above; legacy kit/template workflows require assets that are not distributed here. The private sample-kit packaging routine has been removed from this source package.

Third-party plugins and their trademarks remain their respective owners' property. They are not bundled or licensed by this repository. No software license grant is added by this source publication; refer to an explicit repository license if the owner adds one.

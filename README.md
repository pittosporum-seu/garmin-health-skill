# Garmin Health Skill

[中文说明](README.zh-CN.md)

A privacy-first, read-only Codex/Hermes skill for exporting personal Garmin Connect health and activity data, including all-day heart-rate series, sleep HRV, sleep stages, Pulse Ox, respiration, stress, Body Battery, activity streams, and original FIT records.

It is intended for a data owner analysing their own account. Garmin Connect availability depends on device model, firmware, region, subscriptions, and which metrics were recorded.

## Highlights

- Broad daily coverage: stats, heart rate, HRV, sleep, stress, Body Battery and events, steps, SpO₂, respiration, intensity, readiness, floors, RHR, fitness age, lifestyle, and nutrition logs/settings.
- Normalized time series with source fields retained, including all-day heart rate and sleep-embedded HRV.
- Original activity detail streams, optional route polylines, and FIT decoding for records and RR-interval (`hrv`) messages.
- Historical range export with a secure per-day checkpoint and `--resume`, so an interrupted export can continue without refetching completed days.
- Safe-by-default exports: health payloads require an owner-only `--output PATH`; `--stdout` is an explicit opt-in for terminal disclosure.
- Per-endpoint availability and consistent structured errors rather than failing an entire multi-source report when a device does not support a metric.

## Install

Clone the repository directly into the skill directory, then create its isolated environment:

```bash
git clone https://github.com/pittosporum-seu/garmin-health-skill.git \
  ~/.hermes/skills/garmin-health
cd ~/.hermes/skills/garmin-health
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

```bash
PY=~/.hermes/skills/garmin-health/.venv/bin/python
CLI=~/.hermes/skills/garmin-health/garmin_health_cli.py
$PY $CLI status
```

The default token directory is `~/.hermes/skills/garmin-health/tokens`. To isolate an account, set `GARMIN_TOKENSTORE` to a directory you control.

If a saved token expires, provide credentials only through environment variables—never command-line arguments or files:

```bash
GARMIN_EMAIL='account' GARMIN_PASSWORD='password' $PY $CLI login
```

`login` creates the token directory with owner-only permissions. Tokens are never printed, exported, or committed.

## Stable releases and updates

The installed Hermes skill is a Git checkout at `~/.hermes/skills/garmin-health`. Releases are immutable `vX.Y.Z` tags; use the bundled updater to fetch the latest stable tag without touching the ignored `tokens/` directory:

```bash
cd ~/.hermes/skills/garmin-health
bash scripts/update-skill.sh --latest
$PY $CLI --version
```

The updater refuses a working tree with local changes, fetches tags only from this GitHub repository, checks out the selected release in detached mode, verifies that `VERSION` matches the tag, and refreshes runtime dependencies. Preview the selected version first with `bash scripts/update-skill.sh --latest --dry-run`; pin a known release with `--version v1.0.0`.

For a manual install, choose a version from [GitHub Releases](https://github.com/pittosporum-seu/garmin-health-skill/releases) and clone it with `git clone --branch vX.Y.Z --depth 1 ...`. Do not use `git pull` if you expect the install to remain pinned to a release.

## Quick start

All commands that return personal data require a secure output file by default. Add `--stdout` only when the terminal is private and deliberately chosen.

```bash
# Concise daily report
$PY $CLI overview 2026-08-01 --output ~/garmin/overview-2026-08-01.json

# Time series: all-day heart rate and sleep HRV
$PY $CLI series heart-rate 2026-08-01 --output ~/garmin/hr-2026-08-01.json
$PY $CLI series sleep-hrv 2026-08-01 --output ~/garmin/sleep-hrv-2026-08-01.json

# Exact raw endpoint payload when a field is not represented in the summary
$PY $CLI raw body-battery-events 2026-08-01 --output ~/garmin/body-battery-events.json
```

Use `--force` to replace an existing export. Every written JSON file is atomically saved with mode `0600` on POSIX systems.

## Historical exports

The default range includes `stats`, `hrv`, `sleep`, and `training-readiness`. Select endpoint(s) explicitly for a smaller export, or use `--all` for every daily endpoint.

```bash
# A focused, resumable month export
$PY $CLI export-range 2026-07-01 2026-07-31 \
  --kind stats --kind hrv --kind sleep \
  --output ~/garmin/july-core.json

# Continue an interrupted run; range must match exactly and endpoint choices come from the saved file
$PY $CLI export-range 2026-07-01 2026-07-31 \
  --resume --output ~/garmin/july-core.json

# Full daily export (can be large and endpoint availability will vary)
$PY $CLI export-range 2026-07-01 2026-07-31 \
  --all --output ~/garmin/july-all.json
```

Each fully fetched date is immediately checkpointed. Failed or unavailable endpoints are preserved at that endpoint as:

```json
{
  "available": false,
  "error": { "type": "ExceptionName", "message": "…" }
}
```

## Offline analysis

`analyze` reads an existing `export-range` JSON locally. It does not authenticate or make a Garmin network request, which keeps the analysis reproducible from the input file.

```bash
$PY $CLI analyze data-quality ~/garmin/july-core.json \
  --output ~/garmin/july-data-quality.json
```

`data-quality` is the required first step before interpreting a trend. It reports requested-date coverage, missing dates, endpoint errors, availability by endpoint, and sample counts for documented summaries such as sleep duration, sleep HRV, resting heart rate, stress, and training readiness. It does not treat missing data as zero, assess device accuracy, or make medical claims.

`recovery` compares the latest day with up to 28 earlier personal observations for Garmin sleep HRV, resting heart rate, sleep duration, and Garmin training readiness. A metric needs at least 7 prior measurements before the result includes a median, median absolute deviation (MAD), and latest-minus-median value; otherwise it reports only the data limitation.

```bash
$PY $CLI analyze recovery ~/garmin/july-core.json \
  --output ~/garmin/july-recovery.json
```

It deliberately does not calculate a replacement “recovery score”, diagnose a condition, or claim why a measurement changed.

`sleep` reports Garmin-provided sleep duration, deep/light/REM/awake components, sleep heart rate, sleep stress, and sleep HRV where available. It gives the latest stage proportions only over the recorded deep/light/REM durations, and applies the same 7-prior-measurement baseline rule to each metric.

```bash
$PY $CLI analyze sleep ~/garmin/july-core.json \
  --output ~/garmin/july-sleep.json
```

Sleep start/end values are kept with their Garmin source field rather than converted when timezone semantics are unclear. This is descriptive sleep-trend analysis, not sleep-disorder screening.

`stress-energy` needs a range export that includes `stats`, `sleep`, `stress`, and `body-battery`. It reports Garmin's daily stress summary, Garmin-provided Body Battery `charged`/`drained` points, and any recognized Body Battery level observations without recomputing a Body Battery score.

```bash
$PY $CLI export-range 2026-07-01 2026-07-31 \
  --kind stats --kind sleep --kind stress --kind body-battery \
  --output ~/garmin/july-stress-energy.json
$PY $CLI analyze stress-energy ~/garmin/july-stress-energy.json \
  --output ~/garmin/july-stress-energy-analysis.json
```

When at least 7 varying same-date pairs exist, the result includes descriptive Pearson correlations for stress with sleep duration/steps and Body Battery charge/drain with sleep duration/steps. These are not causal findings; sleep can cross calendar dates. A Body Battery response whose shape is not recognized is explicitly reported as a limitation and is never coerced into a value.

## Activity and FIT data

```bash
$PY $CLI activities --limit 20 --output ~/garmin/activities.json
$PY $CLI activity 623002723 --output ~/garmin/activity.json
$PY $CLI activity-stream 623002723 --include-route \
  --output ~/garmin/activity-stream.json
$PY $CLI fit-summary 623002723 --output ~/garmin/fit-summary.json
$PY $CLI fit-stream 623002723 --message record \
  --output ~/garmin/fit-records.json
$PY $CLI fit-stream 623002723 --message hrv \
  --output ~/garmin/rr-intervals.json
```

Run `fit-summary` first to discover the messages and fields actually contained in that activity. Its `unrecognized_field_definitions` reports profile fields that the decoder cannot name. `fit-stream` preserves such fields' value, raw value, field number, base type, units, and developer-field flag so data is not silently discarded. `fit-stream --message hrv` exposes RR intervals when the FIT file contains them. The FIT `hrv` message has no absolute timestamp of its own: preserve its message order and align it only with adjacent `record`/`event` messages; do not manufacture beat-by-beat timestamps.

## Other supported data

```bash
$PY $CLI performance 2026-08-01 --output ~/garmin/performance.json
$PY $CLI profile --output ~/garmin/profile.json
$PY $CLI body body-composition 2026-07-01 2026-08-01 \
  --output ~/garmin/body-composition.json
$PY $CLI body hydration 2026-08-01 --output ~/garmin/hydration.json

# Only when the data owner explicitly asks for it
$PY $CLI reproductive menstrual-calendar 2026-07-01 2026-07-31 \
  --output ~/garmin/menstrual-calendar.json
```

The `raw` daily endpoint choices are shown by `raw --help`. They include `all`, `stats`, `heart-rate`, `hrv`, `sleep`, `stress`, `all-day-stress`, `body-battery`, `body-battery-events`, `steps`, `spo2`, `respiration`, `intensity`, `training-readiness`, `morning-readiness`, `events`, `floors`, `rhr`, `max-metrics`, `fitness-age`, `lifestyle`, and nutrition data.

## Data semantics and limitations

- Garmin Connect generally presents all-day heart-rate timeline data as two-minute averages. The skill preserves source gaps and does not interpolate them.
- Garmin HRV Status is a sleep-derived Garmin metric reported in milliseconds. It is not equivalent to a timestamped beat-to-beat RR series. Garmin describes HRV Status as using RMSSD and a personal baseline; see its [HRV Status overview](https://www.garmin.com/en-US/garmin-technology/health-science/hrv-status/).
- Values, data fields, and cadence vary by device and account. An unavailable endpoint is not evidence that a physiological measurement was zero.
- Garmin health metrics are not medical diagnosis, treatment, cure, or prevention tools.
- This project uses the community `garminconnect` client to read a personal Garmin Connect account. It is not an official Garmin SDK or a substitute for the [Garmin Health API](https://developer.garmin.com/gc-developer-program/health-api/), which is a separate business program.
- FIT records follow Garmin's [FIT protocol](https://developer.garmin.com/fit/protocol/); field availability depends on the source device and activity.

## Development

```bash
$PY -m pip install -r requirements-dev.txt
$PY -m pytest -q
$PY garmin_health_cli.py --help
```

Maintainers: bump `VERSION`, update the release-facing documentation, commit to `main`, then create and push the matching `vX.Y.Z` tag. The tag workflow verifies the match and creates the GitHub Release with generated notes.

## License

[MIT](LICENSE)

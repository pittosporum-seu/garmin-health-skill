# Implementation Plan: Personal Garmin Health Analysis

## Overview

Add an offline, read-only analysis layer on top of `export-range` files. It must describe personal trends and data quality without diagnosing disease, inventing missing observations, or exposing health data by default.

## Architecture decisions

- Analyse an existing `garmin-health-range` JSON export rather than making hidden network calls. This makes the result reproducible and keeps collection separate from interpretation.
- Keep analysis logic in `garmin_health_analysis.py`, using only the standard library. The CLI owns authentication and secure output; the module owns parsing, baselines, evidence, and limitations.
- Require an output file or explicit `--stdout`, use personal robust baselines (median and MAD), and report coverage/sample counts with every conclusion.
- Avoid clinical thresholds and causal claims. All results include a non-medical notice and metric-specific limitations.

## Task list

### Phase 1: Foundation and data quality — Complete

**Acceptance criteria**

- Parse and validate a versioned `export-range` file without Garmin credentials or network access.
- Add `analyze data-quality` with date coverage, endpoint availability, missing dates, and metric sample counts.
- Add focused pytest fixtures for complete, missing, and endpoint-error exports.
- Document the command and its privacy boundary in both READMEs.

### Phase 2: Recovery baseline

**Acceptance criteria**

- Add `analyze recovery` using available sleep HRV, resting HR, sleep duration, and training-readiness evidence.
- Compare the latest valid day with a prior personal robust baseline; do not emit a result when there are insufficient samples.
- Include evidence, baseline, coverage, confidence, and explicit limitations in pytest-verified output.

### Phase 3: Sleep analysis

**Acceptance criteria**

- Add `analyze sleep` for duration, stage distribution, awake time, sleep HR, sleep stress, and sleep HRV when present.
- Detect only descriptive schedule/duration variation; do not diagnose sleep disorders.
- Test missing fields, zero values, and a normal range export; update both READMEs.

### Phase 4: Stress and energy analysis

**Acceptance criteria**

- Add `analyze stress-energy` for daily stress, Body Battery charging/draining, and their relationship to sleep/activity evidence.
- Preserve unknown Body Battery source shapes as a limitation instead of guessing values.
- Test supported and unsupported shapes; update both READMEs.

### Final checkpoint

- Run the full pytest suite, `py_compile`, dependency check, and skill validation.
- Confirm every phase has its own Git commit and is pushed to `main`.
- Update repository documentation and the local Garmin maintenance memory.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Garmin endpoint shapes vary by device | Use tolerant extractors, expose field availability, and test unsupported shapes. |
| Wearable metrics are mistaken for diagnosis | Use personal baselines, descriptive language, and a mandatory non-medical limitation. |
| Health data leaks through analysis output | Reuse existing secure-output policy and never write exports or fixtures containing real data. |
| Small samples create false trends | Report sample count/coverage and suppress baseline comparisons below the minimum. |

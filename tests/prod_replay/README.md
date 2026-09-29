# Production replay harness

Replays saved production episodes through the real recut, validator and pass-2 reconciliation code (no LLM calls).
Set `MINUSPOD_PROD_FIXTURES` to a fixtures directory holding `expectations.json`, `baseline.json` and `episodes/<id>/`.
Without the variable every test here is skipped. Fixtures hold instance data and are never committed.
Run: `MINUSPOD_PROD_FIXTURES=/path/to/fixtures PYTHONPATH=src .venv/bin/python -m pytest tests/prod_replay -q -rxXs`
Replay outputs (cut lists, validator decisions, gate results, cluster summary) are written to `<fixtures>/replay_out/`.
Tests marked `report` only write those outputs; `-m 'not report'` runs just the checks.
Not reconstructable from saved data: stored audio analysis (the recut replay passes none) and pass-2 finding confidence (0.95 is assumed; the drop branch ignores it).
Pass-1 cut lists are rebuilt from saved markers and checked against the logged render span count; a mismatch skips.
Global settings other than the category action map use install defaults.

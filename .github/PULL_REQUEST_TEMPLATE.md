## Summary

What does this change and why?

## Test plan

- [ ] `PYTHONPATH=src pytest tests/` passes
- [ ] `ruff check src/ tests/` passes
- [ ] Frontend (if touched): `npx tsc --noEmit`, `npm run lint`, `npm run test`
- [ ] UI changes: `npm run design:check` and `npm run test:browser` pass
- [ ] UI changes: before/after evidence identifies routes, viewport widths, input modes, and data limitations
- [ ] UI changes: failure/retry, keyboard, dirty-state, and destructive-action behavior checked where affected
- [ ] CHANGELOG.md updated

For larger changes, please open an issue first so the approach can be
agreed on before you write code.

# Final verification

UTC: 2026-10-25T15:41:22.366072+00:00. No paid API calls, commits or pushes. Server stopped after checks.

## Clean copy

Location: `/private/tmp/rubiks-clean-submission-tq0u_q4n`. Copied prospective commit files using `git ls-files --cached --others --exclude-standard`, skipped missing tracked paths. Real `.env`, `.venv`, private SQLite and BFS cache were not copied. A fresh Python 3.10.0 venv installed all pinned requirements; Kociemba built successfully. Package installation used PyPI after sandbox DNS escalation; no model API was involved. Fresh `.env` came from the example with only public student identity and a placeholder key.

README steps verified: `python3 -m venv .venv`; activation; `python -m pip install -r requirements.txt`; template configuration; `python scripts/build_bfs.py`; `python scripts/run_benchmark.py --build-baseline`; `python scripts/make_readme_results.py --check`; `python -m pytest backend/tests -q`; `python scripts/check_flow.py`; and the documented uvicorn command. Both generated README blocks reproduce exactly. The fixed-seed byte-determinism and independent-oracle tests pass.

Final clean test run: **510 passed**, one existing Starlette/httpx deprecation warning, no failures. Offline audit: **0 outbound requests, 0 real HTTP transport attempts**. See clean-tests.xml and offline-outbound.json. Local pre-final full suite was 509 passed; the subsequently added dashboard-scope regression test is included in the final clean suite of 510. No implementation changes followed this test run.

## Browser checks

Loaded `/` and `/dashboard.html` from the clean copy. Default mode SIMULATED and exact student identity visible. Completed one free four-mock race in the clean copy only. Replayed saved real C1 with REPLAY banner; replay loads its saved data once and performs no inference. The primary dashboard shows paired primary-001/002, denominators 2 and Opus1/2, plus a separate partial C3 table preserving INFRA_ERROR. Corrected screenshot: ../img/dashboard-primary.png. Replay screenshot: replay-check.png. Both original and clean-copy request ledgers remained **458**; no real key was present in the clean app. No new LIVE record was created.

## Evidence and cleanup

Original LIVE trials/statuses and all billing attempts are preserved. Public billing export omits reusable consent credentials and is tested for one-time clean bootstrap without resetting existing counters. Archive manifest lists every moved file and reason; no deletion. Real .env and local virtualenv were untouched. Diagnostic/request JSON and alternative-prompt evidence remain because their provenance/tests make them ambiguous to remove. Verification recreated ignored caches locally; those are excluded from the submission copy.

## Security and Git

Working tree (including private local ledger) and every reachable Git revision scanned. Unexpected secret hits: 0. The only credential-bearing file is expected ignored `.env`; matching values were never printed. Authorization-syntax hits are source/placeholder/redaction references, not detected credentials. See security-scan.json for paths/categories. Git history currently contains one commit/one blob, so no historical experiment commit is claimed. Current branch main; origin matches supplied repository: True. No Git mutation was performed.

## Author review remaining

Write the collaboration disclosure; review the five clearly labelled draft answers; confirm inspected video checkpoints 0:20/3:15/4:00; decide whether to replace the historical closing slide, which includes C3 and does not match the corrected paired scope. Repo URL and branch are filled from supplied inputs. Video is 13,143,797 bytes, exactly 272.795 seconds, original duration unchanged. Header readable at all six inspected samples; not an every-frame audit.

## Manual Git commands (not executed)

```bash
git status --short
git add README.md .gitignore .env.example requirements.txt pytest.ini backend frontend scripts stages data docs demo results decisions.md flow.md
git diff --cached --stat
git diff --cached
git commit -m "Prepare Assignment 02 submission"
git push origin main
```

Review the recording-scope warning before final submission. The unrelated tracked deletion `assignment 2/empty` is not included by the explicit add paths above.

# Spend reconciliation — 2026-10-25

Recorded UTC: 2026-10-25T13:39:35.807683+00:00. Owner-reported OpenRouter total is authoritative for conservative budget headroom; it was not independently fetched. Approximately 90K tokens is an owner-reported account figure, not an allocation to recorded requests. The supplied as-of time, dashboard view and date range were placeholders; their actual values are unknown.

| Item | USD |
|---|---:|
| Authoritative reported spend | 0.11 |
| Known attempt charges (120 requests) | 0.027262678 |
| Unexplained difference | 0.082737322 |
| Historical holds covered and released | 2.70 |
| Active holds at checkpoint | 0 |
| Unchanged harness cap | 4.00 |
| Remaining headroom | 3.89 |

27 attempt costs remain unknown. No historical charge, attempt body, hold, count or model allocation was rewritten. An append-only SQLite reconciliation checkpoint covers attempt IDs through 120. New attempts retain normal reservations; future costs are added above the reconciled basis. No next stage is authorized.

Whether earlier sessions made any paid request absent from this ledger, including reverted optimization work: **unknown**. The preserved reports, local original-thread history, restore scripts/tool-call captures and backups did not establish a specific extra missing paid request. They cannot prove complete billing coverage. Preserved snapshots show 26 diagnostic attempts, then 30 including Phase 3 smoke, then 90 Stage W attempts. Account attribution changed historically and is not independently verified.

Possible causes of the gap, none established: charges on the 27 responses lacking usage.cost; unpreserved earlier/reverted-work calls; different key/account/date-range scope or other shared-account activity; rounding or reporting-time differences. The gap is not assigned to a model, run or suite and is not fabricated as per-request costs.

Verification: 500 offline tests passed; check_flow passed; real HTTP transport attempts and outbound requests both zero. Attempt-row SHA-256 before and after: 9b2181f8d033269fafda3db11effb3c1a4f232ab9be9d9bab525b19dd40a7107. Limits, prompts, adapters, experiment settings and statistics remain unchanged. No paid calls, commits or pushes.

Evidence: docs/verification/reconciliation/checkpoint.json and tests.xml. Earlier COST_LEDGER/COSTS files remain historical snapshots, not updated invoices.

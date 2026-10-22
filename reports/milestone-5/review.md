# Milestone 5 deployment checkpoint

**Historical checkpoint from 16 October 2026.** The [19 October publication status](../../docs/publication-status.md) records the later collection interruption and zero qualifying days. The startup evidence below does not establish uninterrupted operation afterward.

Local deployment is running. The seven-day evaluation is collecting prospective data, so **the full milestone remains incomplete**.

The owner chose their Windows computer. The per-user limited task `BitcoinPaperAgent-01a129e0` runs a supervised, paper-only worker independently of this chat and resumes at sign-in. Keep the computer awake, signed in and online. No power-plan or sleep-protection setting was changed.

## Review the current state

- [Updating evaluation report](../../var/forward/reports/evaluation.md)
- [Local paper-alert inbox](../../var/forward/inbox/index.html)
- [Deployment controls and methodology](../../docs/deployment.md)
- [Verification snapshot](verification.json) and [98-test record](test-results.json)

The run began **2026-10-16 14:02:48 Asia/Riyadh**. Its boundary is **2026-10-23 14:02:48 Asia/Riyadh**. At this checkpoint, completed days are **0/7**. Only actual public quotes count; the short earlier demos, indicator-warming candles, constructed tests and offline elapsed time do not fill the week.

## Evidence captured

All 98 automated tests passed, including cash/inventory conservation for baseline books, duplicate/restart protection, source/config/environment freeze, quote-age coverage, missing days, backup restoration and actual Windows worker PID/library loading. A constructed seven-day test verifies the coverage calculation and is explicitly separate from this live run.

The live supervisor owns the actual worker process. One guarded interruption stopped that worker after verifying its parent, token and ledger path. The supervisor restarted it automatically. Candidate cash, BTC, fees and the single fill stayed exactly unchanged through the interruption, and the local alert remained a single delivery. Fresh quotes and features resumed, and the observation log records the resulting gap.

A consistent online SQLite backup was independently audited and compared with the pre-interruption account. The candidate and all three baseline books reconcile. At the snapshot, all **76** accepted candidate quotes had paired forward observations. The old foundation account, milestone-4 demo and raw research checksum remain unchanged.

The timed account uses $10,000 simulated funds and frozen defensive rules. Scheduled DCA, buy-and-hold and cash books receive the same observations and modeled costs. Google edits and LLM advice are disabled in this run; notifications are local. No funded trade, external message or paid model call occurred.

The first brief Windows startup check exposed a venv-launcher extra process. It was stopped and archived separately before the timed run, and the monitored child now matches the actual worker PID. That short check is not counted toward the week.

## What remains

Wait for the full observed window, inspect each day's declared 99% quote-age coverage and integrity criteria, reconcile ledgers/backups, and review incidents and comparisons. The program closes the window and stops after seven elapsed days; insufficient coverage will be reported as a failed criterion, not hidden or presented as a completed reliable week. Seven days cannot establish durable investment returns.

Docker/Compose packaging is prepared but unbuilt because Docker is absent. No cloud deployment was performed. The chosen Windows deployment is the verified runtime for this checkpoint.

Implementation stops here for owner review while the already authorized collector continues. Milestone 6 remains pending.

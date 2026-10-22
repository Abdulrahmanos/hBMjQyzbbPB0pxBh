# Local deployment and prospective evaluation

The owner chose their Windows computer for milestone 5. The project was deployed under the per-user Task Scheduler task `BitcoinPaperAgent-01a129e0`, using public Coinbase quotes, simulated funds and local notification files. Docker packaging is prepared for a later host; Docker is not installed on this computer, so no container build or cloud deployment has been verified.

**19 October status:** the latest read-only check requires attention after interrupted collection, and zero days qualify. The recorded gap prevents the original window from meeting its seven-day coverage requirement. See the [publication checkpoint](publication-status.md) for dated evidence. The controls below describe the installed deployment; a saved healthy snapshot does not confirm its current state.

The timed run started on **16 October 2026 at 14:02:48 Asia/Riyadh** (11:02:48 UTC). Its seven-day boundary is **23 October 2026 at 14:02:48 Asia/Riyadh**. That is an observation window, not a promise of seven qualifying days or profitable results.

## Check progress

The supervisor refreshes these files approximately once a minute:

- `var/forward/reports/evaluation.md`: readable results, completed/qualifying days and limitations.
- `var/forward/reports/evaluation.json`: observations, daily coverage, configuration and source versions.
- `var/forward/health.json`: a saved health snapshot. Use the health command below for a fresh check.
- `var/forward/logs/worker.jsonl`: ticks, fills, feature refresh, notification delivery and failures.
- `var/forward/logs/supervisor.jsonl`: worker exits, restarts and backup/report failures.

From the project root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m bitcoin_agent health
.\.venv\Scripts\python.exe -m bitcoin_agent forward-report
.\.venv\Scripts\python.exe -m bitcoin_agent forward-audit
```

The health command returns exit code 0 only when the supervisor and worker heartbeats, accepted quotes and features are fresh. It is read-only and uses no network calls. A healthy process is distinct from a qualifying seven-day experiment. Read the actual generated timestamp when looking at a saved file after the computer has slept.

To refresh the local inbox while the collector continues:

```powershell
.\.venv\Scripts\python.exe -m bitcoin_agent --config config/forward.json --db var/forward/ledger.sqlite3 integration-demo --settings config/integrations.forward.json --output var/forward/inbox-preview.json
```

Then open `var/forward/inbox/index.html`. The periodic collector writes individual local message JSON files immediately; the command above refreshes the human-readable HTML index. It does not place trades or send external messages. Weekly reporting stays Monday 09:00 Riyadh and may cover only part of this experiment's first/last week. Its period differs from the experiment's Friday-to-Friday window.

## Keep the run operating

**Keep the computer awake, signed in and connected to the internet.** Closing the chat does not stop the scheduled task. Sleep, shutdown, sign-out, connectivity failures and Task Scheduler/service problems can interrupt collection. The logon trigger resumes the supervisor when this user signs in again. No power-plan setting or sleep protection was changed. No password is stored by the project, and the task uses a limited interactive user principal.

The supervisor runs one worker. OS locks protect both supervisor and worker; process death releases locks without deleting a stale PID file. Windows workers launch the actual Python interpreter with the same virtual-environment library directories, so the supervisor's process handle identifies the worker itself rather than the venv redirector. A token and heartbeat tie workers to their supervisor. Worker failures restart with a 5-to-300-second backoff. Stalled worker recovery first requests a graceful stop, then terminates only the exact child handle if needed. Short outages remain visible in the evaluation.

The registered task starts at user logon, ignores duplicate task instances and retries a failed supervisor up to three times at one-minute intervals. `StartWhenAvailable` does not wake a sleeping computer or give the application offline market observations. The launcher scripts also support manual startup:

```powershell
.\deploy\windows\Start-PaperRun.ps1
.\deploy\windows\Stop-PaperRun.ps1
# After an intentional stop, resume the same account explicitly:
.\deploy\windows\Start-PaperRun.ps1 -Resume
```

The stop file is honored on later task launches. It preserves cash, BTC, orders, risk state and history; it does not liquidate positions. All holdings here are simulated. In-flight I/O can delay shutdown. The supervisor normally allows up to 75 seconds for worker shutdown before terminating its child.

After the seven-day boundary, the supervisor closes the observation window, writes the final report, creates `var/forward/COMPLETE` and stops. A later logon does not trade past that marker. Resume does not reopen a completed window; preserve the evidence and define a separate experiment if another run is needed. Successful completion of the time window is separate from passing the coverage criteria.

## Frozen experiment and comparisons

This run starts with a fresh $10,000 simulated account in `var/forward/ledger.sqlite3`. The earlier foundation, research, operational demo and short startup check remain separate. The strategy uses the milestone-3 defensive operational rules; swing entries remain disabled. Regime-based target changes are part of those fixed rules. Google edits and the LLM are disabled for this timed run, and notifications remain local.

The run records its full configuration, checksum, Python/library versions, platform and application source checksum. Resume rejects a changed source, configuration or environment instead of silently combining different policies. Do not edit `bitcoin_agent/*.py`, the forward settings or the installed calculation libraries during this window. Documentation and saved report work can continue without changing the trading policy.

Each accepted prospective quote records the candidate's committed mark and updates three separate baseline books:

| Book | Rule |
| --- | --- |
| Candidate | Fixed defensive DCA/regime/risk policy, including existing-inventory exits |
| Scheduled DCA | Same initial DCA sleeve reference, daily tranche and rolling seven-day spending cap; no dip rule or defensive exit |
| Buy-and-hold | Buys available initial cash over as many accepted quotes as needed under the same modeled per-quote fill cap, then holds |
| Cash | Holds the full initial USD amount; earns no interest |

Every book includes idle cash. Purchases use the accepted ask, adverse modeled slippage, observed price/quantity increments, cent rounding, modeled fees and market minimums. No final liquidation cost is deducted from marked equity. The candidate retains exposure/cash/loss limits that intentionally differ from the passive baselines; exposure differences are part of the strategies being compared. The $1,000 per-quote fill cap is an artificial paper limit, not measured exchange liquidity.

The public-stream worker records observations only after an accepted engine tick commits. Quote IDs prevent duplicate baseline accounting after replay/restart. A crash may omit an observation between the candidate commit and the comparison record; the deployment audit reports any such difference for review. Candidate fills remain authoritative in their own transactional ledger. The comparison is a prospective paper experiment, not a claim of exact exchange counterfactual execution.

Historical candles warm indicators only. They never contribute elapsed evaluation days or replace quotes missed while the program was offline. The first accepted quote starts the clock. The daily bins are consecutive 24-hour periods anchored to that instant, rather than partial calendar dates.

A day's quote coverage measures the time covered by actually received quotes before their configured age limit expires, capped by the next observation. With this configuration, both receipt/exchange timestamps must be at most 120 seconds old. A complete day qualifies at at least 99% quote-age coverage. Each of the seven days must qualify; no configuration change or recorded observer-integrity failure is allowed for the experiment to mark its evidence complete. The report also records the maximum observation gap and fraction of observations with fresh features. Coverage does not establish that every intervening price or drawdown was observed.

Seven days can demonstrate operational behavior under those seven days' conditions. They cannot establish durable profitability, a safe personal allocation or protection against every market gap.

## Backups and recovery evidence

The supervisor creates an initial observed-state backup and then approximately hourly backups under `var/forward/backups`. SQLite's online backup includes committed WAL content. Each snapshot passes SQLite integrity checks and independent candidate/baseline ledger audits before publication; its metadata includes a SHA256. Existing backup files are never overwritten.

To make another snapshot:

```powershell
.\.venv\Scripts\python.exe -m bitcoin_agent backup-paper --output var/forward/backups/manual-unique-name.sqlite3
```

Backups remain on this computer and do not protect against loss of the whole disk. A backup represents its own earlier point in time; it must not be mistaken for unbroken subsequent observation history. No automatic restoration overwrites the live ledger. For a recovery rehearsal, audit a backup at a separate path. Actual worker restarts normally reopen the current SQLite/WAL ledger and retain cooldowns, holdings and fill IDs.

One controlled worker interruption was performed during startup verification. The script checked the actual worker PID, parent supervisor, ledger path and token before stopping only that worker. The supervisor restarted it, fresh quotes resumed and the candidate retained exactly the same cash, BTC and fill count. The maximum observed collection gap was recorded. The initial short check that exposed Windows' launcher process indirection is archived in `var/forward-startup-check-20261016` and does not count toward the timed week.

## Ordinary continuous service with Docker

The Dockerfile uses the official Python 3.14 slim Bookworm image, pinned calculation-library versions, an unprivileged numeric user and explicit application/config copies. The build context excludes `.env`, local state, raw data, private Sheet metadata, credentials and reports. Compose uses a persistent named volume for state, a read-only root filesystem, no published ports, dropped capabilities, bounded logs and a quote-freshness health command. It runs `run-paper --cycles 0` continuously until stopped, retrying a failed process up to five times. Normal stops are not automatically restarted. After a Docker daemon/host restart, run Compose again. This ordinary service does not resume or relabel the archived timed experiment.

```powershell
docker compose config
docker compose build
docker compose up -d
docker compose exec paper python tools/check_service_health.py
docker compose logs --tail 50 paper
docker compose down
```

The actual [GitHub Actions build](https://github.com/Abdulrahmanos/qytbcf1pULIvwxUO/actions/runs/39750052488), Compose validation and container configuration command passed on Linux. Docker is unavailable on the development Windows host, and no cloud service or uninterrupted deployment was demonstrated. Start a new service account rather than moving the Windows evaluation database across an environment change. Do not run two deployments against the same SQLite file. To enable real notifications after account setup, use the override and instructions in [notification-setup.md](notification-setup.md).

## Primary references

- [Docker Compose services and health/restart options](https://docs.docker.com/reference/compose-file/services/)
- [Official Python container tags](https://hub.docker.com/_/python)
- [Register-ScheduledTask](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/register-scheduledtask?view=windowsserver2025-ps)
- [New-ScheduledTaskPrincipal](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtaskprincipal?view=windowsserver2025-ps)
- [SQLite online backup](https://docs.python.org/3.14/library/sqlite3.html#sqlite3.Connection.backup)
- [Python subprocess process handles](https://docs.python.org/3.14/library/subprocess.html) and [installation-library paths](https://docs.python.org/3.14/library/sysconfig.html)

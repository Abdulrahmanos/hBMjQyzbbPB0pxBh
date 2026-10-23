# Completion and publication plan

## Current owner direction — 24 October 2026

The owner requested real LLM use and a new week of evidence, chose an always-on server and their own OpenAI API key, then requested a free hosting alternative. AWS Free account plan is the proposed host; the owner is creating the account and configuring their key locally. See docs/cloud-evaluation.md. No new experiment has started and no real OpenAI call has yet been verified.

New cloud packaging uses a distinct account/volume and read-only shadow adviser with a 28-attempt lifetime budget. The current source revision adds provider receipt recording without changing execution policy. Old source remains preserved in Git commit 8a9d0b19b8db57f31736f7a63c7b8af18c31faed. Do not resume or relabel the October accounts using this changed source. Do not recreate the old chat heartbeat or Windows task.

Only use the owner's key from the explicit private file; an inherited executor OPENAI_API_KEY is not authorization to use that credential. Do not create paid resources or upgrade accounts. Verify the Free plan and credits before deployment. GitHub source publication remains authorized; YouTube and external submission remain excluded. Real model proof does not establish improved returns, and seven days must actually elapse on the new server.



## Current owner direction — 22 October 2026

This section supersedes the older plan below. The owner explicitly requested finishing the historical LSTM comparison and publishing the GitHub repository, then supplied the complete Apziva brief and requested real Telegram/Gmail integration. GitHub source publication to `Abdulrahmanos/qytbcf1pULIvwxUO` is authorized again; other uploads and Apziva submission are not. YouTube remains waived. No new heartbeat or milestone stop is authorized or needed.

The LSTM experiment is complete: all three seeds choose reserve in every test week, matching the no-model cash baseline. The real-data logs have been exported honestly with separate accounts and gaps. They do not prove an uninterrupted week or 24/7 reliability. The earlier 99% daily gate was an additional protocol, not a numerical threshold in the supplied brief.

The obsolete `var/forward` collector was gracefully stopped and its verified Windows task disabled on 22 October, preserving the account and original experiment dates. Do not restart it, reset it or recreate the deleted chat heartbeat. The old frozen application source remains unchanged. New work is isolated in `research_lstm`, tools, reports and deployment packaging.

Review `reports/submission/requirements.md` for actual readiness. Real Telegram BUY/SELL delivery has now been verified with two synthetic fills and provider receipts; see reports/submission/telegram-verification.json. The completed setup used the owner's dedicated bot token and verified private chat; a phone number or private username alone is insufficient. Gmail needs Desktop OAuth authorization in addition to the recipient address. Secrets and personal destinations belong only in ignored `.env`/`var`. Setup helpers and a credential-readiness check are prepared. Do not claim real delivery without provider receipts.

Publish the source, README, tests and truthful reports using `tools/publish_source.py`; the older publisher retains the stricter failed-week gate and must not be used to claim its experiment passed. Verify the remote commit/README and actual CI result. Update the requirements audit if the container build passes. The owner will submit the repository link to Apziva.

## Archived previous plan

The following records earlier scope and experimental history. Its publication prohibition was superseded **only for GitHub** by the newer explicit owner instruction above.

Latest owner instructions: **do not upload anything** and **YouTube is not a requirement**. These revoke earlier upload/publication authorization. Continue and finish the project locally, keeping the owner informed of meaningful milestones. No GitHub push, repository creation, video upload or external submission is authorized. The previously discussed GitHub destination is retained only as historical configuration.

## Current experiment

**20 October interruption:** Windows recorded lid-triggered Modern Standby at 19 October 18:28:33 UTC and later battery-budget hibernation. The next prospective quote arrived at 20 October 06:21:15 UTC, a gap of approximately 11 hours 53 minutes. Day 1 has already exceeded its 864-second uncovered-time allowance, so the 19–26 October window cannot pass. The existing supervisor recovered collection on the same account; do not silently reset or re-anchor it. Preserve the failed window and its audited snapshot. A question is pending about keeping this computer powered, signed in, online and with its lid open for a separate future test. Until answered, continue monitoring the current account as interrupted operational evidence. Do not repeatedly notify the owner of this same known coverage failure. See reports/milestone-5/interruption-review-2026-10-20.md and var/completion/incidents/20261020-overnight/.

The interrupted October 16 account is preserved at `var/archive/forward-20261016-interrupted`, with a separately audited final snapshot. The October 19 recovery rehearsal is preserved at `var/archive/forward-20261019-recovery-rehearsal`. Neither counts toward the replacement window.

**21 October follow-up:** another lid-triggered overnight interruption produced a 16-hour-39-minute quote gap. The existing supervisor recovered automatically after resume; a proposed manual recovery was withheld before any task/process mutation because its fresh-heartbeat guard detected recovery. Current collection and ledger integrity pass, but days 1 and 2 cannot qualify. The same availability question remains pending; do not repeatedly notify the owner of the known power constraint or start another experiment. Evidence: reports/milestone-5/interruption-review-2026-10-21.md and var/completion/incidents/20261021-overnight/incident.json.

The new timed experiment uses `var/forward/ledger.sqlite3`, `config/forward.json` and `config/integrations.forward.json`. Only historical candle cache was copied to warm indicators; accounts and prospective observation logs started fresh. Application source checksum after the reliability fix: `660ef78760b834ad45148540da41a43b31fe61dcd611c7e7d1fa21197d7c3b39`. Do not edit bitcoin_agent source or the forward settings/calculation libraries during the window.

The corrected Windows task uses pythonw.exe, one-minute recovery triggers for eight days, and an idle-sleep prevention request held only while its supervisor lives. The display can sleep. Explicit sleep, lid closure, exhausted battery, sign-out, shutdown and disconnection can still interrupt collection. Power-plan settings were not changed. The recovery rehearsal verified replacement of an interrupted supervisor with unchanged cash, BTC, fees and fill count; all 101 implementation tests passed. See reports/milestone-5/recovery-2026-10-19.json.

## Progress and finalization

The hourly heartbeat `Complete Bitcoin project locally` (automation id `complete-bitcoin-evaluation-and-publish`) was deleted on 22 October after the owner questioned the repetitive background work. Do not recreate it without a new owner request. The Windows paper collector is a separate process; deleting this chat follow-up did not stop it, reset the account or alter its original deadline. The availability question remains pending. A healthy process is separate from a qualifying experiment. Never replace missing prospective quotes with historical or constructed data.

Read health and audit using the existing CLI. Produce interim review materials with:

For the scheduled local check, run `.\.venv\Scripts\python.exe -B tools/check_forward_progress.py` after reading this plan and the saved state. It audits source/config/environment freeze, paired quotes, balances, backups and uploads-disabled settings, and records a local check. It also flags an already impossible daily coverage budget or a failed completed day, even if the collector has recovered. An intentionally stopped collector at the completed seven-day boundary is handled separately from an unexpected outage. This helper is outside the frozen application source and does not alter the strategy or any observation.

```powershell
.\.venv\Scripts\python.exe -B tools/finalize_project.py --output reports/final
```

This takes a consistent, independently audited local backup and writes an explicitly incomplete report until every gate passes. At the end of the real window, the supervisor stops and writes COMPLETE. Then run:

```powershell
.\.venv\Scripts\python.exe -B tools/finalize_project.py --require-complete
```

The finalization gate checks all seven individual days, public-stream provenance, source/config/environment freeze, quote pairing, candidate and three baseline accounts, no observer/config incidents, and a closed window with stopped worker/supervisor. A failed criterion prevents finalization; do not lower a threshold to finish early. The original data and policy comparisons remain distinct from the historical research selector.

## Local completion and upload prohibition

Finish the readable local evaluation and checklist, with an optional local walkthrough. Preserve both favorable and unfavorable results. Record unverified optional integrations/Docker honestly. Do not claim seven days establish durable investment returns.

The connected GitHub and Windows credential manager were checked earlier, and a local Git repository was initialized. No remote repository was created and no source/video was uploaded. config/submission.json sets uploads_authorized=false and youtube_required=false. The retained publisher rejects upload requests before credential or network access. Never change uploads_authorized without a NEW explicit owner instruction after this prohibition. The original private verification copy is at var/publication/verification.milestone-4.private.json.

The owner explicitly clarified that YouTube is not required. Do not ask for a YouTube account or public video URL, and do not treat an upload as an outstanding completion gate. The prepared narrated preview remains a local optional artifact. No other external publication/submission is authorized either.

After verified local completion, report local artifact paths, stop the hourly heartbeat, and preserve this chat. Do not archive the chat automatically.

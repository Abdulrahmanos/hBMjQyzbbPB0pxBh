# Configuration, shadow advice and demo delivery

Milestone 4 implements the optional integrations and demonstrates them through a **local project inbox**. No email address, Telegram account or API key is needed to inspect the demo. The inbox contains confirmed paper fill alerts and a Monday report, based on the actual saved paper ledger.

A private configuration Sheet was created through the connected Google Drive account. Native values, tab name, frozen headers, privacy and layout were checked. Its identifier and native readback remain in ignored local files. This connection belongs to Codex. It does not supply credentials to the Python process. A new installation supplies its own GOOGLE_SHEET_ID; the included demo uses config/sheet-rows.json.

## Local demonstration

Run from the project root:

```powershell
.\.venv\Scripts\python.exe -m bitcoin_agent integration-demo
.\.venv\Scripts\python.exe -m bitcoin_agent report-preview
.\.venv\Scripts\python.exe -m bitcoin_agent outbox-status
```

Open `reports/milestone-4/inbox/index.html`. `integration-demo` scans existing fills, schedules the latest eligible weekly period and delivers previews to deterministic local files. Running it again preserves message IDs and sends no duplicate preview. On a fresh clone with no accepted quotes or fills, the inbox is empty. It does not invent a portfolio history.

To generate new observations with simulated execution and the demo integrations:

```powershell
.\.venv\Scripts\python.exe -m bitcoin_agent --config config/demo.json run-paper --cycles 8 --integrations config/integrations.demo.json --output reports/milestone-4/public-demo.json
```

`--cycles 0` runs in the foreground until Ctrl+C. Quote ticks, feature refresh and integrations use separate paths. No background service is left running after a bounded command. Continuous cloud deployment and seven consecutive observed days remain milestone 5.

## How Sheet edits work

The runtime reads `Settings!A5:C64`: Setting, Value, Meaning. Every supported setting must appear exactly once. Values are native numbers/booleans or a JSON array of DCA, swing and reserve fractions. Fractions use decimals, so `0.03` means 3%. Unknown paths, duplicate/missing rows, formulas returning invalid values, nonfinite numbers and inconsistent allocations are rejected. Spreadsheet formulas are never executed by Python; it reads their returned values and applies the same validation.

Only the paths in `remote_config.CONTROLS` can be edited remotely. Initial funding, market identity, fees/slippage, feature definitions, reporting schedule and core risk controls remain in the owner's local baseline. Remote tranche size, rolling spending cap, per-position risk and holding time cannot exceed their local bounds. Bear and unknown targets remain anchored to the local policy. Sheet cells displaying local risk limits are explanatory; editing them does not change the engine.

Each refresh validates the complete candidate and its funding checksum before writing an atomic JSON cache. A malformed remote response retains the last accepted configuration. A corrupt cache falls back to the separately validated local baseline. An invalid local baseline prevents startup. Thus an accepted configuration is always required for new entries. The cache records baseline/candidate checksums and fetch time; changing the local baseline invalidates the old cache.

The default refresh interval is 3,600 seconds. Failed fetches use the same retry interval. The demo reads `config/sheet-rows.json`, a verified copy of the initial Sheet values. It **does not poll the live Sheet**. For live reads, select `config_source: google` and provide the independent read-only OAuth setup below.

When the main quote loop accepts a changed configuration, it cancels pending buys and releases reservations, records the checksum, invalidates the old feature snapshot and recomputes cash targets on a later eligible tick. It retains inventory, cost basis, historical fills, weekly spending, cooldowns and lifetime risk state. Existing swing stop/target prices are never recalculated by the edit. New-entry ATR settings apply to future positions.

## Why the LLM is a shadow adviser

The historical selector remains research-only. There is no evidence yet that LLM allocation advice improves outcomes. Milestone 4 records advice alongside the current rule-based portfolio so a later controlled experiment can test that question. **No LLM output changes configuration, risk limits or orders.**

The allowed suggestions are `keep_rule_targets`, `prefer_cash` and `prefer_mixed`. Input contains completed-bar features, timestamp, config checksum, a small portfolio summary and risk limits. It contains no secrets, headlines or external tools. The Responses request uses a strict JSON schema, `store: false`, a maximum of 300 output tokens, an eight-second network timeout and no automatic retry. Local validation checks the option, keys and explanation lengths even after provider schema validation. Incomplete, malformed, refused or unavailable responses fall back to the unchanged rule system.

Persistent limits allow at most four attempts per UTC day and at least six hours between attempts. Failures consume quota. Context is limited to 4,096 UTF-8 bytes and the complete request to 12,000 bytes. These bound request frequency and payload/output size, **not an exact dollar bill**. Choose an explicit account-supported model and set the provider project's spending limit before paid use. The model, prompt version, input and result/status are stored in `adviser_records`; nothing is applied to trades.

The demo provider is `deterministic-demo-fixture`. Its record is explicitly a test response, not an LLM call. Actual API performance, account model availability, billing and investment value have not been verified. Promotion to a trading influence would require an approved separate policy, prospective data, matched costs/funding, per-regime comparisons and evidence of benefit beyond deterministic controls. Reusing the already observed milestone-2 holdout would not be independent validation.

## Delivery and report accounting

Confirmed fills are scanned into `notification_outbox` under `fill:<fill ID>`, so a crash after a fill commit cannot lose its notification intent. Notification I/O takes place after releasing the trading transaction. It cannot undo the fill or place another trade. Local files use deterministic IDs and atomic replacement.

For live providers, transient known failures retry with persisted backoff. Bad credentials/requests become `blocked`. Timeouts or interrupted claims that may have reached a provider become `uncertain`. They require inspection and explicit retry:

```powershell
.\.venv\Scripts\python.exe -m bitcoin_agent outbox-retry --id "<observed message ID>"
```

A retry of an uncertain network delivery may duplicate a message. Telegram and Gmail delivery is not claimed to be exactly once. A deterministic email Message-ID is included as a diagnostic identifier, not a provider deduplication guarantee.

The default weekly boundary is Monday 09:00 Asia/Riyadh, equivalent to 06:00 UTC. UTC is also supported. The service queues the latest eligible scheduled period once and catches it up after a restart. It does not send every missed historical week in a burst. A running process is required; the project has not created a Codex automation.

Reports use `(start, end]` accepted observations and fills. Opening equity uses the last accepted mark at/before the boundary, or initial simulated cash for a partial first week. Closing equity uses the last accepted mark at/before period end. Later fills/balances cannot leak into an earlier report. Net return is closing/opening minus one. Observed drawdown is the largest decline from a peak within that marked sequence, seeded by opening equity. Fees and realized P&L sum the in-period immutable fills. Incident counts summarize recorded rejected quotes, feed/feature failures and rejected configuration refreshes.

No accepted observations produces unavailable marked results. Partial periods and boundary marks older than 120 seconds are labeled. Sparse quotes do not establish continuous drawdown. The mark is cash plus BTC at the bid/ask midpoint; unrealized exit costs are not deducted. The weekly payload is frozen when queued. `report-preview` creates a new local draft without changing that delivered record.

## Optional real accounts later

Keep populated credentials in ignored `.env` or deployment secrets, never chat or the Sheet. Environment variables take precedence over literal `.env` values. The loader evaluates no code and performs no interpolation. The adapters currently accept an existing OAuth refresh token; initial interactive consent is a separate owner setup step.

| Capability | Private setup required |
| --- | --- |
| Read Google Sheet | Enable Sheets API in an owner-controlled Google Cloud project; authorize `https://www.googleapis.com/auth/spreadsheets.readonly`; store GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN and GOOGLE_SHEET_ID |
| Telegram alerts | Create an owner-controlled bot, start its chat, store TELEGRAM_BOT_TOKEN and numeric TELEGRAM_CHAT_ID |
| Gmail report | Enable Gmail API; authorize `https://www.googleapis.com/auth/gmail.send`; store GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN and GMAIL_REPORT_RECIPIENT |
| OpenAI shadow adviser | Store OPENAI_API_KEY and an explicit LLM_MODEL supported by that account; configure provider spending limits |

You do not need to create those accounts to review this project. If connecting them later, complete the provider's normal consent flow privately and use only the required scopes. `config/integrations.live.example.json` demonstrates the settings. Switching delivery to live and starting it sends queued paper alerts/reports to the configured destinations, including any pending earlier records; review `outbox-status` first and use a separate database for isolated external setup tests.

The code uses fixed provider endpoints with bounded reads, timeouts and redacted error codes. No funded exchange credentials are consumed. Cloud secret storage, service management, deployment monitoring and the seven-day evaluation are still pending.

## Primary API references

- [Google Sheets values.get](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get)
- [Google OAuth offline access](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Gmail messages.send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send) and [MIME/base64url message construction](https://developers.google.com/workspace/gmail/api/guides/sending)
- [Telegram Bot API sendMessage](https://core.telegram.org/bots/api#sendmessage)
- [OpenAI Responses Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)

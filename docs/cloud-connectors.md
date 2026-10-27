# AWS Google Sheets and Telegram

Verified on 27 October 2026. The [redacted evidence](../reports/submission/aws-connectors-verification.json) records real API reads, Telegram provider receipts and deployment isolation.

## What runs

- **Telegram:** a separate worker scans committed candidate paper fills every 30 seconds. It sends side, BTC quantity, sleeve, reason, price, notional, fee, original trade time and fill ID. Two existing trades were delivered as labelled catch-up alerts. New fills receive normal paper-trade alerts. Run-plus-fill identifiers and a persistent outbox prevent duplicates on restart or re-scan. Transient failures retry with backoff; ambiguous delivery stays uncertain for review instead of risking a duplicate.
- **Google Sheets:** a dedicated service account reads `Settings!A5:C64` hourly, validates all 16 supported controls and writes an atomic `validated-config.json` cache. Only the configuration Sheet is shared as Viewer. The account has no project IAM roles or domain-wide delegation. It requests only the Sheets read-only scope.
- **Frozen experiment:** Sheet edits are staged, not applied to trades. The current account keeps its original settings, source, libraries and 2 November 09:52:34 Asia/Riyadh deadline. Applying changed controls requires an explicitly declared configuration change or a later experiment; the worker does not auto-enable edits when the deadline arrives.

The first authenticated AWS read and cache passed at 12:51:41 UTC. The hourly schedule starts from that attempt, including after a restart. A rejected Sheet value or remote failure retains the last valid cache; without a valid cache, the local baseline remains available. Tests verify excessive-risk edits, cache corruption and provider failure. Funding and protected defensive allocations cannot be changed by the Sheet.

The small open model remains a separate shadow adviser. Neither this integration nor a model receipt establishes profitable trading.

## Deployment and secrets

The [installer](../tools/install_cloud_connectors.py) creates a separate `bitcoin-connectors` Compose project using the exact immutable image of the healthy paper container. It does not recreate, stop, rebuild or reconfigure that collector. Both services mount its evidence read-only, use independent state volumes, run as UID 10001, drop capabilities and expose no ports. Gmail remains separate.

Place the following reviewed source helpers together on the server: `cloud_connectors.py`, `install_cloud_connectors.py`, `cloud_gmail.py` and `install_cloud_gmail.py`. Verify their hashes against the chosen source commit before execution. The deployment uses `/opt/bitcoin-connectors`.

Private inputs, transferred through an authenticated encrypted channel, are:

- `private/telegram.env`: only the existing `TELEGRAM_BOT_TOKEN` and verified numeric `TELEGRAM_CHAT_ID`.
- `private/sheets.json`: an object containing `sheet_id` and the standard `service_account` credential object. The latter contains `type`, `client_email`, `private_key_id`, `private_key` and the official Google `token_uri`.

Keep these files outside Git. The host private directory is mode 0700; Telegram credentials are 0600. The Sheets JSON is owned by container UID 10001 and mode 0400 under that root-only directory, then mounted read-only into only the Sheets worker. No credentials appear in command arguments, logs, reports or source.

The current Sheets private key was generated directly on AWS, and only its public certificate was uploaded to Google. It expires on 27 October 2027. The unusable initial download key was removed with owner approval. Unlike Gmail's Testing OAuth refresh token, this service-account flow does not require a weekly user sign-in. Authentication uses Google's documented RS256 flow with OpenSSL performing the signing; token and API destinations are fixed to official Google hosts.

Run the installer only after the private inputs exist:

```bash
sudo python3 /opt/bitcoin-connectors/install_cloud_connectors.py
```

The installer requires an already healthy paper collector, performs a real Sheets read, sends any unsent existing paper fills once, and enables both workers with `restart: unless-stopped`. Reruns preserve existing outboxes and reject a changed deployment definition.

## Read status without exposing credentials

```bash
sudo docker exec bitcoin-connectors-telegram-1 python tools/cloud_connectors.py telegram status
sudo docker exec bitcoin-connectors-sheets-1 python tools/cloud_connectors.py sheets status
sudo docker logs --tail 20 bitcoin-connectors-telegram-1
sudo docker logs --tail 20 bitcoin-connectors-sheets-1
```

Status contains redacted receipt state and timestamps, not tokens or personal destinations. Sheets `source_validated` and `applied_to_trades: false` are expected during this test. Review `needs_attention`, stale refresh times or an `error.json` newer than the last successful status. Do not erase an outbox to retry an uncertain notification.

Seven new focused tests and 33 existing notification tests passed. GitHub Linux checks for the deployed source also passed. Future notifications still depend on the server, credentials and provider network; one successful check does not certify uninterrupted operation.

# Real Telegram and Gmail setup

The adapters are implemented, and their payloads, retries, duplicate protection and Monday boundary are tested. **Real delivery is not verified until your own credentials are supplied and the provider returns a receipt.** A local demo is not an external delivery test. Never paste tokens, passwords, client secrets or refresh tokens into chat or GitHub.

The project owner completed real Telegram delivery verification on 22 October 2026: one synthetic BUY and one synthetic SELL passed through the persistent outbox, both received provider receipts, and re-scanning did not resend them. See the [verification record](../reports/submission/telegram-verification.json). These invented prices are not market observations. Gmail send-only OAuth and one real test email were verified on 26 October; [AWS weekly installation is still pending](cloud-gmail.md). A fresh clone needs its own credentials using the steps below.

## Telegram

1. In Telegram, use the verified **BotFather** account to create your project bot yourself.
2. Run `python tools/setup_telegram.py` in your local terminal and enter the token at its hidden prompt.
3. Open the helper's link yourself and press Start in your private chat. A one-time random challenge identifies your chat, so an unrelated message cannot become the destination.
4. The helper saves `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in ignored `.env`. A private username or phone number is not a substitute for that chat ID. Use a dedicated project bot, not a shared production bot.

Each confirmed buy or sell queues one alert containing side, BTC quantity, sleeve, reason, execution price, notional, fee, time and fill ID. Failed delivery does not undo a fill or submit another trade. An ambiguous provider timeout is held for review instead of blindly repeating the message.

## Gmail

1. In your Google Cloud project, enable the Gmail API, configure the OAuth consent screen for your own test user, and create a **Desktop app** OAuth client yourself.
2. Download its client JSON to `var/secrets/gmail-client.json` (ignored). Do not commit it.
3. Run `python tools/setup_gmail_oauth.py` locally. Complete Google sign-in and consent yourself. The helper requests only `gmail.send`, validates OAuth state, uses PKCE and a loopback callback, and saves the offline refresh token to `.env` without printing it.
4. Set `GMAIL_REPORT_RECIPIENT` in `.env` to your own email address.
5. External Google OAuth apps in testing can have expiring refresh tokens; check your consent configuration before unattended operation. No successful setup or permanent access is assumed.

The application's weekly boundary is **Monday 09:00 Asia/Riyadh**, equivalent to 06:00 UTC. The running service catches up the latest due report after a restart and uses a persistent period ID. It must be running and connected to deliver on time. Closing a laptop can delay delivery; an always-available cloud host is needed for reliable 24/7 operation.

## Verify and activate

```powershell
.\.venv\Scripts\python.exe tools/check_notifications.py
# These commands actually send clearly labelled tests to the configured destinations:
.\.venv\Scripts\python.exe tools/check_notifications.py --send-test both
```

The readiness report in `var/integrations/notification-readiness.json` contains missing variable **names** and receipt status, never credentials. Both provider receipts must be observed before claiming real delivery works.

For a new operational account, use `config/notifications.paper.json` and `config/integrations.notifications.json`:

```powershell
.\.venv\Scripts\python.exe -m bitcoin_agent --config config/notifications.paper.json --db var/notifications-paper/ledger.sqlite3 run-paper --cycles 0 --runtime var/notifications-paper/market --integrations config/integrations.notifications.json --output var/notifications-paper/last-run.json
```

This is simulated trading with real notifications. It does not enable Google Sheet edits or LLM calls. Those integrations have separate setup. The old frozen `var/forward` experiment is not changed or reused. No chat heartbeat or Windows task is installed by these commands.

Provider references: [Telegram Bot API](https://core.telegram.org/bots/api), [Gmail Python setup](https://developers.google.com/workspace/gmail/api/quickstart/python), [Google Desktop OAuth and PKCE](https://developers.google.com/identity/protocols/oauth2/native-app).

# Gmail reports alongside the frozen cloud experiment

The real Gmail send-only OAuth flow and one labelled test email were verified on 26 October 2026. The provider returned a receipt. Addresses, client secrets, refresh tokens and the receipt ID remain in ignored local files. See [the redacted record](../reports/submission/gmail-verification.json).

**AWS setup verified on 27 October 2026:** the server-originated connection test was delivered in one attempt with a provider receipt, and the separate weekly sender is running with automatic restart enabled. A follow-up check found exactly one outbox item and no error. The paper container, image and original start remained unchanged. Scheduled Monday delivery has not yet occurred.

## Isolation and schedule

- A separate Docker Compose project, `bitcoin-gmail`, uses the **existing paper container's immutable image ID**. It does not rebuild, restart, edit or replace the collector or model.
- Paper evidence is mounted read-only. The Gmail worker has its own volume, outbox and run binding, and only its own four Gmail credentials.
- The existing report calculations use accepted marks and fills up to Monday 09:00 Asia/Riyadh (06:00 UTC). Future quotes are excluded, and partial periods and stale marks remain labelled.
- A persistent run-and-period identifier prevents repeat emails after restart. An ambiguous send is held for review. The worker checks every 30 seconds, so delivery is due at 09:00 with ordinary network/processing delay.
- Reports summarize the paper account; they do not certify the separate seven-day coverage audit or imply that the shadow LLM controls trades.

For the run that began on 26 October at 09:52 Riyadh, the first due report is **2 November at 09:00 Riyadh**. It is a partial weekly report. The experiment's original seven-day boundary is 09:52:34 that day and is unchanged.

## Installation procedure (already completed on the owner server)

Prepare the four fields from the locally authorized `.env` as a one-line JSON file under ignored `var/secrets/cloud-gmail.json`: `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `GMAIL_REPORT_RECIPIENT`. Do not copy the whole project .env to the server; other integrations have separate credentials.

Place the reviewed `tools/cloud_gmail.py` and `tools/install_cloud_gmail.py` beside each other in a **separate** server directory such as `/opt/bitcoin-gmail`. Use the exact approved Git commit and verify the files' SHA-256 hashes. Do not pull new code into the frozen `/opt/bitcoin` checkout.

Run in the existing EC2 terminal:

```bash
sudo python3 /opt/bitcoin-gmail/install_cloud_gmail.py
```

At its hidden prompt, paste the one-line private JSON directly from your local file, then press Enter. It is not a shell command and is not saved to shell history. Do not paste it into chat or take a screenshot of it. For the verified owner deployment, a one-time RSA-4096 OAEP SHA-256 envelope avoided the clipboard issue: the server decrypted the four fields, validated them through the same installer routine, saved them with root-only access, and removed the temporary transport key. No plaintext credential was placed in shell history or public artifacts.

The installer checks the existing paper container identity, health and evidence volume, stores only Gmail fields in a root-private directory, and sends a labelled AWS connection test using a persistent outbox. Only after that test succeeds does it start the separate worker. Look for `GMAIL_SERVER_READY`; then confirm the second test email in the destination inbox.

The existing Docker Compose installation must support `env_file.format: raw` (Compose 2.30+), which prevents credential interpolation. Never print `docker compose config` with credentials loaded; use `config --quiet` if needed.

Read status without exposing secrets:

```bash
sudo docker compose -f /opt/bitcoin-gmail/compose.gmail.json exec -T mailer python tools/cloud_gmail.py status
```

Stop only the email worker if needed; this leaves all paper collection and model services untouched:

```bash
sudo docker compose -f /opt/bitcoin-gmail/compose.gmail.json stop mailer
```

## Authorization lifetime

The current Google app is External / Testing. Google issues these Gmail-scope refresh tokens with a **seven-day lifetime**. Authorization completed on 26 October afternoon, so the planned 2 November 09:00 report is before that expiry; revocation or other account changes can still invalidate access earlier. Longer-running weekly delivery needs renewed authorization or an appropriate production consent setup. No permanent access is claimed.

Sources: [Google refresh-token expiration](https://developers.google.com/identity/protocols/oauth2#expiration), [Docker raw environment files](https://docs.docker.com/reference/compose-file/services/#format).

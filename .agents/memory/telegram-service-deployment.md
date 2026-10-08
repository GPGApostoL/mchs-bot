---
name: Telegram service deployment
description: Deployment constraint for running Telegram polling alongside the Mini App API.
---

Run the Telegram long poller and Mini App API as one always-running service. Avoid multiple bot pollers for the same token.

**Why:** Telegram permits only one active `getUpdates` poller per bot token, and quiz sessions are held in process memory, so multiple instances can conflict and restarts can discard active tests.

**How to apply:** Keep a single VM deployment and one Run workflow unless polling is deliberately separated and session state is moved to shared storage.

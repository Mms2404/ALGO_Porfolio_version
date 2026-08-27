# Algo Trading Platform — Portfolio Edition

A multi-user automated options trading backend: Django + Celery + Redis + Supabase, with a Flutter mobile client. This is a **sanitized version** of a real, live production system, built to demonstrate the engineering, not give away the trading edge. What's kept as-is: strategy names, traded indices (NIFTY/SENSEX), general scheduling cadence — these are standard options-trading concepts, not secrets. What's redacted: the actual entry/exit decision logic, exact profit-target/stop-loss thresholds, and strike-selection math — see `demo_strategy.py` for the real plumbing every strategy shares, with placeholder logic standing in for the real thing.

## What it does

One strategy engine runs *once* per signal, independent of how many users are subscribed to it. When it decides to enter or exit a position, that decision fans out — every subscribed user gets their own order placed on their own broker account, with the trading mode (LIVE/simulated) configurable per strategy, not globally.

## Architecture

```
apps/
  execution/     signal → fan-out-per-user executor (DRY/LIVE switch, idempotency,
                 balance verification, position-verified exits)
  strategies/    strategy engines (real decision logic redacted — demo_strategy.py
                 shows the shared plumbing every real strategy uses; base.py,
                 test_strategy.py included as-is)
  marketdata/    scrip master caching (Supabase Storage, gzip-compressed, survives
                 redeploys), expiry resolution, ATM/OTM strike math, live feed
                 (REST + WebSocket), NIFTY-50 reference data
  scheduling/    Celery tasks + Beat schedule
  admin_api/     admin-only trigger endpoints (preview/confirm pattern, role-gated)
  accounts/      user-facing account verification + balance endpoints
core/
  angel/         broker adapter — SmartAPI auth, order placement, balance (RMS),
                 order-status stream, multi-account rate-limit fallback
  supabase/      single DB access point, server-side JWT verification
  crypto/        RSA-OAEP (static creds) + Fernet (session tokens)
  conditions.py  market-hours/holiday gate, per-user balance gate
  notifications/responses/logging  small shared infra utilities
```

Everything under `core/angel/` and `apps/marketdata/` implements Angel One's *publicly documented* SmartAPI — auth flow, order placement, quote/websocket streaming — included as-is since there's nothing proprietary in wiring up a published broker API. The actual trading logic lives entirely in `apps/strategies/`, and that's the part that's redacted.

## Engineering highlights

- **Idempotent by design.** Every signal is uniquely constrained at the database level (strategy, day, kind, instrument). A duplicate task invocation — which *does* happen in production, more than once — can't create a duplicate trade. Caught this exact failure mode live twice (see bug log #7, #15) and closed both.
- **Position-verified exits.** Before selling anything, the system checks whether a given user actually got that specific leg filled at entry — not just "were they subscribed." Prevents a partial-fill scenario (one leg bought, one rejected for insufficient funds) from turning into a naked, unhedged position or a phantom sell of something never owned.
- **Per-strategy LIVE/DRY, not a global switch.** Some strategies can run against real money while others simulate, independently, controlled per Celery Beat entry.
- **Cross-process, redeploy-proof caching.** With 5 concurrent Celery worker processes, a naive per-process cache meant redundant downloads of a large instrument reference file. Moved to a shared, gzip-compressed cache in Supabase Storage — measured 24x size reduction, and it survives container redeploys (a local file cache doesn't, on platforms like Railway).
- **Real broker constraints handled directly:** IP whitelisting, multi-account rate-limit fallback, exchange-segment routing (NSE/BFO) resolved automatically per instrument, order-placement timing logged in milliseconds per leg.
- **Django + Celery gotcha, diagnosed and fixed:** `django.setup()` was never called in the Celery entrypoint — meant every application-level log line vanished silently in the worker, while Celery's own framework logs still appeared, making it look like tasks were hanging. Root-caused via elimination, not guesswork.

## Bug log

[`BUG_LOG.md`](./BUG_LOG.md) — 25 real bugs found across static review and live production runs, each with symptom, root cause, and fix. Sanitized the same way as the code (no strategy specifics), but every engineering detail is real.

## Stack

Django · Celery · Redis · Supabase (Postgres + Storage + Auth) · Flutter · Railway (deployment)

## Config

Both `.env.example` (blank) and `.env` (filled with fake, freshly-generated placeholder values — including a newly-generated RSA keypair and Fernet key, never used against anything real) are included so the required configuration shape is visible without reconstruction. Nothing in either file connects to a real Supabase project, broker account, or domain.

## What's *not* here

The actual strategy logic — entry conditions, exit thresholds, strike selection — is the real trading edge and isn't published. `demo_strategy.py` shows the exact plumbing every real strategy shares (signal creation, idempotency guard, fan-out, mode handling) with placeholder decision logic standing in for the real thing.

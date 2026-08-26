# ALGO Project — Bug Log

A running record of every bug found (static review + live runtime) and how each was fixed, kept in chronological order. Includes items still open at the bottom.

---

## Pre-deployment (static code review)

### 1. Strategy C crash — `AttributeError` on first run
**File:** `apps/strategies/strategy_c.py`
**Symptom:** `StrategyC.run()` referenced `self.instrument`, but `__init__` never set it — every real invocation would crash immediately.
**Fix:** Added `instrument` param to `__init__`/`run_strategy_c()`/`run_strategy_c_task`, matching the pattern already used in `strategy_b.py`/`strategy_a.py`.

### 2. `_build_legs()` silently dropped the futures leg
**File:** `apps/execution/executor.py`
**Symptom:** `overnight_strategy` (PUT + FUTURES) only ever got its PUT leg executed — the futures leg never generated an order, execution row, or notification, in LIVE mode.
**Fix:** Extended `_build_legs()` to recognize `fut_symbol`/`fut_token`, with enforced ordering (PUT before FUT on entry, FUT before PUT on exit, per your compulsory-order requirement).

### 3. Notification text showed a bogus strike for FUTURES legs
**File:** `apps/execution/executor.py`
**Symptom:** Trade notifications for a FUT leg displayed the PUT's strike price next to it (futures have no strike of their own).
**Fix:** `_notification_message()` now omits the strike for FUT legs specifically.

### 4. Debug `print()` statements leaking secrets — **flagged, not fixed**
**File:** `apps/accounts/views.py`
**Status:** Still open — deliberately deferred, never circled back to.

---

## Security

### 5. JWT signature bypass (critical)
**File:** `core/supabase/client.py`
**Symptom:** `verify_supabase_access_token()` fell back to `verify_signature: False` on any decode failure — a hand-forged token with any payload would be silently accepted.
**Fix:** Replaced local JWT decoding entirely with `auth.get_user(token)` — Supabase's own server-side verification, no fallback path that skips signature checking.

---

## Feed / broker connectivity

### 6. No fallback when a feed account hits its rate limit
**Files:** `core/angel/feed_login.py` (new), all 6 strategy files
**Symptom:** Each strategy hardcoded one `FEED_ACCOUNTS[i]` — if that account was rate-limited or blocked, the whole strategy aborted.
**Fix:** New `login_feed_account()` tries every configured feed account in order, falling through on failure.

### 7. Duplicate scheduled-task invocation
**Found:** live DRY run, 2026-07-13
**Symptom:** Two separate Celery workers ran the same scheduled Strategy A task concurrently — each did its own scrip master download, feed login, and websocket connection. The `signals` table's unique constraint correctly blocked the second one's ENTRY/EXIT from actually firing, so no duplicate trade reached the user — but the second run still burned ~86 minutes of a worker slot, a feed connection, and a scrip master download for nothing.
**Root cause of the double-firing itself:** never fully identified — flagged as worth checking Beat's own startup logs.
**Related fix:** `--concurrency=5` (see #8) reduces the chance of this starving other strategies even if it recurs.

### 8. Too few Celery worker slots (H-3)
**Symptom:** Refreshing tokens + Strategy A + Strategy B scheduled minutes apart could exceed default worker concurrency, causing later tasks to queue for hours behind an earlier long-running strategy.
**Fix:** Documented and adopted `celery -A algobackend worker --concurrency=5`.

### 9. Shared feed architecture — explored, not built
**Decision:** Considered a single shared "TickBus" per feed account; decided against it (a plain websocket already supports many token subscriptions per connection, so the original concern didn't hold). Kept the per-strategy-socket design, relying on the feed-account fallback (#6) instead.

---

## Order execution correctness

### 10. IP not registered with Angel SmartAPI
**Symptom:** Live orders rejected: `"<IP> is not a registered IP"`.
**Resolution:** Not a code bug — the broker's API portal requires the server's actual public IP whitelisted per API key. Registered the server's static IP against the correct app entry.

### 11. Live order failures were completely silent
**File:** `apps/execution/executor.py`
**Symptom:** `_live_run()` wrote failure reasons to the database but never logged anything to the console — a rejected or exception-raising order looked identical to a successful one in the Celery log.
**Fix:** Added explicit `logger.info`/`logger.error` for every outcome (placed / rejected / exception), each with the real reason.

### 12. Balance never checked before placing live orders (M-2)
**Files:** `apps/execution/executor.py`, `core/conditions.py`
**Symptom:** `can_user_execute()` existed but was never called — a user with insufficient funds could still get a live order attempt.
**Fix:** `_process_user()` now fetches live balance once per user (LIVE mode only) and compares against total required margin before placing any leg; insufficient users are skipped and notified.

### 13. Partial fills could leave naked, unhedged positions
**Found:** live LIVE run, 2026-07-16 (INDEX_2 Strategy A) — insufficient funds meant CE placed but PE rejected, leaving a naked CE.
**File:** `apps/execution/executor.py`
**Fix:** `_fan_out_exit()` now checks, per user, which legs actually succeeded at entry (`signal_executions.status`) before the EXIT fan-out — a leg that was never filled is never sold, and a user with zero filled legs is skipped entirely at exit.

### 14. The above fix itself had a bug — DRY-mode users always skipped at exit
**Found:** live DRY run, 2026-08-03
**Symptom:** `_get_user_placed_legs()` only checked `status == "placed"`, but `_dry_run()` writes `status == "dry"` on success — every DRY-mode user was incorrectly treated as having zero filled legs at exit time.
**Fix:** Check `status IN ("placed", "dry")`.

### 15. Missing `return` after a blocked duplicate ENTRY — corrupted EXIT signals
**Found:** live DRY run, 2026-08-17 (Strategy A)
**Files:** `apps/strategies/strategy_a.py`, `strategy_b.py`, `strategy_c.py`
**Symptom:** When a strategy's own ENTRY was correctly blocked as a duplicate for the day, the code logged it but didn't stop — it kept monitoring its own (different) prices and eventually wrote a real EXIT signal, with entry prices/tokens that didn't match the actual accepted ENTRY.
**Fix:** Added `return` (with proper stream/task cleanup) immediately after a blocked ENTRY, in all three index strategies.
**Still open:** why a second Strategy A run happened at all that morning was never identified.

---

## Infrastructure / scrip master caching

### 16. Scrip master re-downloaded on every strategy run
**Symptom:** Each of the 5 Celery worker processes has its own private in-memory cache — every process paid the full download cost independently, the first time it personally needed it.
**Fix (v1):** Local file cache shared across processes on one machine.
**Fix (v2):** Moved to Supabase Storage after establishing local files can't survive a Railway redeploy (a fresh container every time, nothing on disk carries over).

### 17. Windows file-write race condition
**Symptom:** Two workers writing the local cache file at the same instant → `WinError 32` (file in use).
**Status:** Identified as benign and Windows-local-dev-only (POSIX/Railway's atomic rename doesn't have this issue). Superseded entirely by the move to Supabase Storage (#16v2).

### 18. `django.setup()` never called — Celery tasks ran with zero internal logging
**File:** `algobackend/celery.py`
**Symptom:** Tasks showed "received"/"Apply" (Celery's own framework logs) but nothing from inside the task itself — `settings.LOGGING` was never applied in the worker process, so every `logger.info()` had no handler.
**Fix:** Explicit `django.setup()` added.

### 19. That fix broke the web (gunicorn) process
**File:** `algobackend/celery.py`
**Symptom:** `algobackend/__init__.py` imports `celery.py` for *any* import of the `algobackend` package — including gunicorn loading `algobackend.wsgi`. The `django.setup()` call failed in that specific boot context (`ImproperlyConfigured: settings not configured`), crashing the whole web service.
**Fix:** Wrapped in try/except — non-fatal there; `wsgi.py`'s own proper `django.setup()` call (confirmed present, standard Django-generated file) still runs correctly right after.

### 20. Old pre-gzip cache object caused a one-time warning
**Symptom:** `"Not a gzipped file (b'{\"')"` — the object in Storage was written before the gzip-compression fix shipped.
**Status:** Not a bug requiring a fix — the existing fallback (treat as unreadable → re-download → re-upload correctly gzipped) handled it automatically; self-healed after one run.

### 21. 12-second delay before Strategy A's ENTRY signal
**Found:** live DRY run, 2026-08-04
**Symptom:** ~5.2s of the delay traced to one worker process's cold Supabase Storage fetch of the scrip master (a cross-process cache miss).
**Fix:** Gzip-compressed the cached object (measured 24x size reduction, 30.7MB → 1.3MB) + a Celery Beat task at 7:00 AM that fires multiple preload copies aiming to warm every worker process's memory ahead of market open.
**Note:** Not a guaranteed sub-3-second fix on its own — several sequential Supabase REST calls elsewhere in the pipeline still add up; flagged as a possible next optimization if needed.

### 22. Stale `celerybeat-schedule` file causing unplanned/duplicate real fires
**Found:** live runs, 2026-08-20 (INDEX_2 Strategy A, twice in one morning)
**Symptom:** At 9:18, a Strategy A run got `409 Conflict` on its own ENTRY — meaning a real INDEX_2 ENTRY already existed before this run, despite no manual or expected trigger having happened yet that day. After deleting that row and changing Strategy A's Beat time to 9:30, a *second* real, independently-running instance appeared again — the visible 9:30 run's own EXIT got `409 Conflict` at 9:34:38, meaning another live instance had already reached EXIT first.
**Root cause:** Celery Beat persists "last run" state to a local `celerybeat-schedule` file, tied to whatever crontab timing was configured when that state was last written. Restarting the worker/beat processes without deleting this file — after changing a strategy's schedule, or even just restarting mid-session — can make Beat treat a task as "overdue" against stale state and fire it for real, independent of any deliberate trigger.
**Fix:** No code change — this is an operational discipline issue. `celery.py` already carries a comment warning about this (`del celerybeat-schedule` after any schedule change); the actual miss was not applying it consistently on *every* restart where timing might have shifted, not just the one restart where code was edited.
**Confirmed:** code was independently checked and ruled out as the cause — `create_signal(kind="EXIT", ...)` in `strategy_a.py` is called exactly once, in a strictly linear sequence after `self._stop.wait()` unblocks; nothing in `_on_tick()`/`_time_monitor()` can trigger a duplicate call from within a single instance.
**Likely also explains item #15** (2026-08-17 double Strategy A invocation) and **item #7** (2026-07-13 double invocation) — same fingerprint (a second, independently-running real instance appearing without a deliberate second trigger), though not confirmed retroactively for those specific dates.

### 23. `{"status": "completed"}` misread as "a trade happened" — not a bug, a naming clarification
**Symptom:** Confusion after a blocked-duplicate run: the task returned `{"status": "completed"}` even though it did nothing (correctly stopped at a blocked ENTRY).
**Clarification:** `"completed"` only ever meant "the task function ran without raising an unhandled exception" — not "a trade occurred." A cleanly-skipped duplicate is exactly as "completed" (in this sense) as a real trade. Not fixed (no code change requested) — noted here so it doesn't cause the same confusion again.

---

## Frontend (Flutter)

### 24. Raw backend/exception errors shown directly to users
**Files:** 8 files across `auth`, `admin`, `profile`, `notifications`, `strategy`, `overnight_trades` features
**Symptom:** Dart exceptions (`SocketException`, `ClientException`, etc.) were passed straight into `Failure`/error states and displayed verbatim in the UI.
**Fix:** Replaced every generic `catch (e) { ... e.toString() ... }` with a context-appropriate friendly message. Supabase's own `AuthException.message` (already user-facing) and backend-crafted response messages were left untouched.

### 25. `DropdownButton` crash on cancelling a stock hedge preview
**File:** `lib/features/admin/application/cubit/admin_state.dart`
**Symptom:** `StockItem` had no `==`/`hashCode` override; reloading the stock list after cancel produced new object instances that no longer matched the previously-selected value by reference, tripping Flutter's "exactly one matching item" assertion.
**Fix:** Added content-based equality on `symbol`.

---

## Investigated and ruled out (not actual bugs)

- **A "worker hanging, zero logs" report citing `--pool=solo`, a DB socket deadlock, and blocking top-level imports** — checked directly against the deployed config and code: `--pool=solo` was not present, this project has no persistent Postgres connection pool to deadlock on (Supabase is accessed via HTTP REST, not raw sockets), and no top-level blocking code exists in any delivered file. Root cause of that specific hang was not resolved; diagnostic logging was added throughout the suspect task chain so the next occurrence is traceable.

---

## Known open items (deferred by choice, not fixed)

- Debug `print()` statements leaking API key/JWT fragments (`apps/accounts/views.py`)
- `producttype` always `CARRYFORWARD`, even for same-day strategies (Strategy A/Strategy B) — accepted risk for now
- DRF endpoints still `AllowAny`; no enforced HTTPS/security headers; `SECRET_KEY` has an insecure fallback default
- `overnight_strategy_v2`'s 4-leg `_build_legs` support — waiting on your strategy spec doc
- Auto-unwind (reversal order) safety net for partial fills mid-sequence — discussed, questions asked (buffer %, failure handling), never implemented
- **Standing operational practice, not a code fix:** delete `celerybeat-schedule` on every restart where any strategy's timing might have shifted — not just the one restart where code was actually edited (see #22). Missing this is the most likely explanation for every unplanned duplicate-instance incident logged above (#7, #15, #22).

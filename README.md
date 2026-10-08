# Multi-Exchange Crypto Signal Bot

A modular backend for researching crypto market signals across Binance and Bybit Spot and
USDT-margined/linear futures. The project supports signals, paper trading, and real-account
read-only views.
It never submits real-money orders, and a confidence score describes how strongly configured
strategy conditions agree—not the probability of profit.

## Current status

Phases 0–3A provide the repository foundation, normalized public market-data adapters, deterministic
analysis, and an explainable directional setup engine for Binance Spot, Binance USDT-M Futures,
Bybit Spot, and Bybit Linear Futures. Phase 3A consumes completed `MarketAnalysis` snapshots without
recalculating indicators and never calls private exchange or order endpoints.

## Architecture

The application is split into exchange adapters, normalized market models, indicators, signal
generation, lifecycle monitoring, paper portfolios, reporting, Telegram delivery, API routes, and
persistence. Strategy code consumes normalized snapshots rather than REST or WebSocket payloads,
which keeps realtime scanning, historical backtesting, and future replay engines interchangeable.

## Local setup

Python 3.12 or newer is required.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,postgres,telegram]'
cp .env.example .env
uvicorn app.main:app --reload
```

The default database is local SQLite. Set `DATABASE_URL` to a PostgreSQL async URL for production.

## Docker setup

```bash
cp .env.example .env
docker compose up --build
```

The API is available at `http://localhost:8000`; health is `GET /health`. Redis is optional and can
be started with `docker compose --profile redis up`.

## Environment variables

See `.env.example`. It contains non-secret development defaults for database connectivity, scoring
thresholds, universe filters, and scan cadence. Keep Telegram tokens, database passwords, and any
future exchange credentials only in `.env` or a secret manager. Public market-data endpoints do not
require Binance or Bybit API keys. Hosts behind a private/corporate TLS authority can set the
standard `SSL_CERT_FILE` environment variable to their trusted CA bundle; TLS verification must not
be disabled.

## Development checks

```bash
pytest
ruff check .
mypy app
```

## Private read-only accounts and live Telegram (Phase 7)

Private account support is opt-in and informational. It is isolated from public market adapters and
from `PaperAccount`; real values are always labeled `REAL ACCOUNT — READ ONLY`, while `/portfolio`,
`/balance`, and `/pnl` continue to show only the independent `PAPER ACCOUNT`. Application code has
no order creation, cancellation, transfer, or withdrawal interface.

Keep credentials only in the untracked local `.env` (or a production secret manager). Create API
keys on the official exchange sites with read-only/account-data permissions. Do not enable trading,
transfers, or withdrawals. Set the relevant feature flag only after the corresponding key and
secret are present:

```dotenv
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=replace_locally
BINANCE_PRIVATE_API_ENABLED=true
BINANCE_API_KEY=replace_locally
BINANCE_API_SECRET=replace_locally
BYBIT_PRIVATE_API_ENABLED=true
BYBIT_API_KEY=replace_locally
BYBIT_API_SECRET=replace_locally
BYBIT_API_REGION=STANDARD
PRIVATE_ACCOUNT_REFRESH_SECONDS=30
```

`BYBIT_API_REGION` must explicitly be `STANDARD`, `EEA`, `TESTNET`, or `DEMO`. `AUTO` is accepted
only as a safe unresolved legacy value: private Bybit connectivity remains disabled so credentials
cannot be sent to a guessed host. The selected hosts are respectively `api.bybit.com`,
`api.bybit.eu`, `api-testnet.bybit.com`, and `api-demo.bybit.com`.

Run harmless validation (identity/account reads only; no Telegram message is sent):

```bash
python scripts/validate_private_connections.py
python scripts/validate_telegram.py
```

Start Telegram polling locally with:

```bash
alembic upgrade head
python scripts/run_telegram.py
```

With Docker, use `docker compose --profile telegram up --build`. The API remains available via the
normal `uvicorn app.main:app` command. Use `/connections` for safe service status; `/accounts`,
`/account_binance`, `/account_bybit`, `/real_balance`, and `/real_positions` show normalized real
account views. API equivalents are `GET /accounts` and `GET /accounts/{exchange}`. Private account
responses are cached and never refreshed per market tick.

Tokens and exchange credentials must never be logged, stored in SQL, returned by FastAPI, or sent
through Telegram. A key that reports trading permission produces a security warning, but the bot
cannot exercise that permission. One failed private connection does not stop public scanning,
Telegram, or paper accounting.

## Automatic runtime symbol universe

The Telegram runtime discovers active USDT markets from public Binance and Bybit instrument and
bulk-ticker endpoints. It filters inactive, low-volume, wide-spread, blacklisted, newly listed
(when listing time is available), leveraged-token, and stablecoin/stablecoin pairs before applying
a deterministic ranking based on core-symbol priority, cross-venue availability, quote volume, and
spread. Binance does not expose a listing timestamp in its existing exchange-info normalization, so
listing-age filtering applies only where that metadata is available.

The cached universe refreshes every 15 minutes by default. A failed refresh retains the last valid
snapshot; if ticker discovery fails before a snapshot exists, only supported and active core symbols
are used as a degraded fallback. Directional selection deduplicates equivalent symbol/market pairs
across venues. Arbitrage projections separately require valid cross-exchange Spot, cross-exchange
Futures, same-venue Spot/Futures basis, or cross-venue Futures funding legs.

Directional scans use 15m, 1h, and 4h candles with bounded concurrency and persist results for
`/top`; Telegram handlers never trigger a synchronous scan. Realtime arbitrage subscriptions are
capped by `ARBITRAGE_RUNTIME_MAX_SYMBOLS_PER_MARKET` (default 10) to avoid connection explosions.
Use `/scanner_status` or `/connections` for safe runtime diagnostics. Validate the complete public
runtime with:

```bash
.venv/bin/python -u scripts/validate_runtime_universe.py
```

Directional decisions are also written to immutable diagnostic audit tables. Each runtime cycle
records expected/evaluated markets and sanitized failures; each Spot BUY and Futures LONG/SHORT
candidate records its score components, terminal hard-filter reasons, selection result, and
persistence action. These audits do not alter classification or execution behavior. The scanner
uses only candles whose close time is at or before evaluation time, matching offline validation and
preventing a still-developing candle from changing a live decision.

Use `/directional_stats` for the compact 24-hour funnel and `/near_misses` for observational
single/two-blocker cases. Near misses are explicitly not signals or entry recommendations. The same
data is available from `GET /signals/diagnostics?window=24h`. A sanitized CLI report combines any
legacy aggregate scan logs with post-deployment audits:

```bash
.venv/bin/python scripts/analyze_directional_24h.py
```

Legacy logs written before the audit migration contain only aggregate winning-direction counts;
per-symbol scores and rejection reasons cannot be reconstructed retroactively.

The default refresh costs four public instrument requests and approximately six bulk ticker/book
requests per 15 minutes. A full directional cycle performs three candle requests per deduplicated
symbol every 15 minutes; realtime active-signal and arbitrage updates use WebSockets.

Configuration is documented in `.env.example`. No private exchange API or trading permission is
used for symbol discovery or scanning, and trading execution remains unavailable.

## 24/7 production runtime

The runtime uses a Unix advisory lock and PID file under `.runtime/`, so a second Telegram polling
process exits without contacting Telegram. It writes redacted JSON logs to
`.runtime/crypto-signal-bot.log`, rotates at 10 MiB, and retains 10 archives by default. The
heartbeat checks Telegram identity, database connectivity, public venue state, Bybit read-only
state, directional scan freshness, realtime book activity, monitoring state, active records, and
WebSocket reconnect counts every 30 seconds. `/system_status`, `/scanner_status`, and `/connections`
are safe operational views.

Set `TELEGRAM_ADMIN_IDS` to receive edge-triggered incident and recovery alerts. An incident is sent
once, repeated failures are deduplicated, rapid flapping is cooldown-protected, and recovery reports
measured downtime. If Telegram itself is unavailable, its incident remains visible in logs and
`/system_status`; delivery resumes only after Telegram connectivity returns.

Manual background controls are:

```bash
scripts/start_bot.sh
scripts/status_bot.sh
scripts/stop_bot.sh
```

Do not run the manual background process alongside launchd or systemd. The process lock prevents a
duplicate, but only one process manager should own restart policy.

For macOS launchd, render the path-neutral template and load it as a user agent:

```bash
mkdir -p .runtime ~/Library/LaunchAgents
PROJECT_DIR=$(pwd)
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
  deploy/macos/com.crypto-signal-bot.plist.template \
  > ~/Library/LaunchAgents/com.crypto-signal-bot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.crypto-signal-bot.plist
launchctl kickstart -k gui/$(id -u)/com.crypto-signal-bot
```

The user agent starts after login and restarts failed processes. A server that must start before any
user login should install an equivalent administrator-owned LaunchDaemon with an explicit service
user. Stop/unload the user agent with:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.crypto-signal-bot.plist
```

For a Linux user service on a VPS, keep the repository at `~/crypto-signal-bot` or adjust the unit,
then install and enable it:

```bash
mkdir -p ~/.config/systemd/user ~/crypto-signal-bot/.runtime
cp deploy/linux/crypto-signal-bot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now crypto-signal-bot.service
loginctl enable-linger "$USER"
systemctl --user status crypto-signal-bot.service
journalctl --user -u crypto-signal-bot.service -f
```

systemd and launchd restart crashes with a delay and send SIGTERM for graceful shutdown. Application
logs remain locally rotated even when the service manager also captures stdout/stderr. `.env`, PID
files, and all runtime logs remain untracked. Trading execution is always `🔒 DISABLED`.

## Indicator methodology

Indicators return their complete aligned series, current value, required history, and an explicit
`sufficient` flag. Missing warm-up values remain `null`; the system never fills them with fabricated
values.

- EMA uses `adjust=False` recursive exponential weighting and supports configurable periods (the
  defaults are 9, 20, 50, 100, and 200).
- RSI, ATR, and ADX use Wilder smoothing. RSI is initialized with the first period's simple average
  gain/loss. ADX measures strength; +DI/-DI provide directional context.
- MACD defaults to 12/26/9 and exposes line, signal, histogram, and histogram change direction.
- Bollinger Bands use a population standard deviation over 20 closes by default, with two standard
  deviations, normalized width, and the close's position within the bands.
- Rolling VWAP uses typical price `(high + low + close) / 3` weighted by volume. UTC daily VWAP is a
  cumulative variant reset at UTC midnight, appropriate for explicitly defined 24/7 boundaries.
- Volume state compares the current candle with a 20-candle mean. Expansion (`>=1.5x`) and
  contraction (`<=0.67x`) constants are documented reference ratios and are not interpreted as
  trade instructions; the baseline length is configurable.
- Volatility regimes use the percentile rank of ATR% within a configurable trailing sample: bottom
  quartile LOW, middle 50% NORMAL, 75th–95th HIGH, and top 5% EXTREME. Insufficient samples remain
  `INSUFFICIENT`.

Trend classification combines price versus EMA20/EMA50, EMA ordering, five-candle EMA20 slope,
+DI/-DI, ADX strength, and confirmed swing structure. Each observation contributes one transparent
point. At least two aligned points are needed for BULLISH/BEARISH and four for a STRONG state. Every
result includes its reasons. These classifications are descriptive features, not predictions.

## Explainable market structure

Swings use a configurable symmetric pivot window. A historical swing is confirmed only after the
required number of later candles exist; candidates near the live edge are returned separately as
developing/unconfirmed. This confirmation delay is inherent and is stored as `confirmation_index`.
The service labels confirmed sequences as higher/lower highs and lows, then reports bullish,
bearish, mixed, or insufficient structure with plain-language evidence.

Support and resistance are ATR-width zones clustered from confirmed swing reactions. A zone exposes
its bounds, midpoint, touches, first/last observation, timeframe, broken state, and transparent
strength: up to 20 points per touch plus up to 20 recency points, capped at 100. Breakouts require
candle closes beyond a zone by a configurable ATR distance and persistence count. A wick that closes
inside is failed, while a single qualifying close is only attempted. Confirmed breakouts can be
classified as pending, held, or failed on a subsequent zone retest. Range detection requires both a
bounded ATR-normalized width and ADX below the conventional 25 strength reference.

## Lookahead protection and data quality

Realtime analysis never promotes a pivot using candles that have not occurred. Tests compare prefix
analysis with later-extended history to protect this property. Ordinary rolling indicators use only
the current and earlier rows. Before analysis, inputs must have one instrument/timeframe, strict
chronological order, unique timestamps, exact expected spacing, valid OHLC values, and nonnegative
volume. Invalid series raise `DataQualityError`; short but valid series return degraded analysis and
explicit warm-up warnings.

`TimeframeAnalysis` contains indicators, trend, structure, volatility, volume, zones, breakout,
retest, and range descriptions. `MarketAnalysis` maps any subset of 1m/5m/15m/1h/4h analyses for the
same exchange market and symbol. The Phase 3A engine consumes this boundary as immutable input.

### Phase 2 limitations

Zones are derived from swing reactions rather than a volume profile. Equal-price pivots are resolved
deterministically rather than treated as separate fuzzy patterns. Breakout volume is context only;
it is not mandatory by default. UTC daily VWAP is an explicit convention, not a natural crypto
session. Structure confirmation necessarily lags by the configured pivot window.

## Directional setup methodology (Phase 3A)

`DirectionalSignalEngine` consumes a precomputed `MarketAnalysis` and returns one typed research
result. Futures evaluate LONG and SHORT candidates; Spot evaluates BUY only and cannot emit a
synthetic short. Results may be NO_TRADE, WATCHLIST, STRONG_SETUP, or VERY_STRONG_SETUP. Confidence
is strategy-condition agreement from 0–100—not a probability of profit.

Hard filters reject mismatched/degraded analysis, stale or future timestamps, extreme volatility,
conflicting trend/structure, failed breakouts, missing structural entry/invalidation, ATR-extended
entries, invalid stops/targets, already-invalidated setups, and poor structural asymmetry. Every
rejection remains visible. Staleness allows one expected primary interval plus configurable grace.

Default score weights total 100: trend 20, structure 20, momentum 15, volume 10, volatility 10,
entry quality 10, 1h/4h alignment 10, and BTC context 5. Each component stores its score, maximum,
reasons, and penalties. Defaults classify 70 as WATCHLIST, 80 as STRONG_SETUP, and 90 as
VERY_STRONG_SETUP. Weights and thresholds are configurable and validated.

Entries are ranges derived from confirmed breakout/retest zones, unbroken support/resistance, or a
confirmed swing with ATR width. LONG/BUY invalidation lies beyond support or a swing low; SHORT
invalidation lies beyond resistance or a swing high. A configurable ATR buffer is applied, and the
stop must remain beyond the entire entry zone within the configured maximum distance.

Targets come from opposing zones, confirmed structural extremes, or measured range projection.
They are ATR-deduplicated, directionally ordered, and limited to one through three. R uses
`ideal_entry` and structural invalidation distance. The best reasonable target must meet configured
minimum R, while TP1 may remain conservative. `paper_exit_plan` defaults to 30/30/40 and normalizes
when fewer targets exist; it is simulation metadata, not personalized real-money sizing advice.

Run read-only public validation and the precomputed-analysis benchmark with:

```bash
python scripts/validate_signals.py
```

The script removes currently developing REST candles and never uses private or order endpoints.

## Persistent signal service (Phase 3B)

`SignalService` wraps the existing Phase 3A engine; it does not rescore setups independently. A
SHA-256 fingerprint combines venue, market, symbol, direction, timeframe, setup/entry type, and the
structural entry-zone bounds. Confidence is intentionally excluded, so ordinary score changes
update one setup. A materially different structural zone creates a new identity. A separate
correlation key groups equivalent cross-venue ideas for presentation while preserving both records.

The pre-entry lifecycle is explicit: `DISCOVERED` promotes to `WATCHLIST` or `WAITING_ENTRY`, after
which only validated promotion, invalidation, or expiry transitions are accepted. Every transition
has a timestamp and reason. Confidence history stores initial/current/minimum/maximum values; a
snapshot row is added only when the configured delta (default 3 points) is reached. Expired and
invalidated records remain queryable.

The bounded-concurrency scanner accepts an exchange-independent analysis provider, reuses its BTC
analysis within a cycle, isolates symbol failures, and produces one canonical structured summary.
Headline counts are computed from that result collection, preventing formatted reports from
diverging from returned setups. Top-5 ranking is separate from confidence and uses confidence,
entry quality, structural R:R, and freshness. It returns at most five and groups obvious venue
duplicates with `alternative_venues` metadata.

Read-only endpoints are `/signals`, `/signals/latest`, `/signals/active`, `/signals/watchlist`,
`/signals/pending`, `/signals/pending/analytics`, `/signals/top`, `/signals/{id}`,
`/signals/{id}/history`, and `/signals/{id}/updates`. List filters
cover venue, market, symbol, direction, lifecycle status, timeframe, confidence, and pagination.
An empty Top-5 is a successful `NO_TRADE` response.

Schema migrations are managed with Alembic:

```bash
alembic upgrade head
alembic downgrade -1
```

Development startup still creates missing tables for convenience; production deployments should
run migrations. The service uses public market data only, has no wallet/order endpoint, and never
requires private Binance or Bybit credentials.

## Realtime signal monitoring (Phase 4)

`MonitoringManager` loads only monitorable signals, groups them by exchange/market/symbol, and
routes normalized public ticker streams from the existing reconnecting WebSocket adapters. It
restores subscriptions and reconciles a REST ticker snapshot after reconnect. A bounded manager
does not subscribe to unrelated markets, and terminal signals are removed on subscription sync.

Pending entry zones come only from the existing confirmed swing, support/resistance, breakout,
retest, and ATR-buffered structural analysis. A strong setup outside that zone is persisted as
`WAITING_ENTRY`, with its reason, basis, invalidation, creation time, and expiry. Ask quotes are used
for BUY/LONG entry evaluation and bid quotes for SHORT. An ATR-normalized proximity event can emit
one `ENTRY_APPROACHING` notification, but it never opens a position.

A quote inside the original zone moves the setup only to `REVALIDATING`. Fresh 15m/1h/4h public
analysis must independently pass trend, structure, momentum, volume, volatility, higher-timeframe,
BTC-context, stop, target, R:R, freshness, breakout, overextension, and spread checks. Confidence is
rescored and its delta is persisted. Stale data, future-dated analysis, excessive spread, material
confidence loss, or structural deterioration invalidates the setup without an entry. A successful
revalidation emits one idempotent `ENTRY_CONFIRMED` event and opens positions exclusively through
the existing paper-account service. Real order execution remains disabled.

`WAITING_ENTRY` records survive restart; an interrupted `REVALIDATING` record safely returns to
waiting and must be evaluated again. `/pending` lists pending BUY/LONG/SHORT setups, while `/top`
keeps separate `ACTIONABLE NOW`, `PENDING ENTRY`, and watchlist sections. Pending analytics compare
immediate-zone and waited entries and report reach, pass/failure, expiry, invalidation, TP/stop,
waiting-time, confidence-change, and R metrics.

Tick-based targets and stops use
the normalized last-traded price consistently. LONG/BUY targets trigger at or above their level and
stops at or below invalidation; SHORT comparisons are reversed. A jump through several targets
records each in order. The last configured target completes the signal whether there are one, two,
or three targets.

Exchange timestamps provide ordering, with receive time retained for data-age diagnostics. Older
events cannot revert state, replayed messages cannot duplicate event keys, and stale quotes cannot
trigger entries, targets, or stops. Fresh data records monitoring recovery. If candle fallback
touches both the next target and stop, `AMBIGUOUS_INTRABAR` is persisted and neither favorable nor
unfavorable path is invented.

Active confidence refresh reuses `DirectionalSignalEngine` output and is scheduled independently
from price ticks (normally on closed execution-timeframe candles, with higher timeframes refreshed
only on their own close). WEAKENING requires both a configurable material decline and multiple
deteriorating strategy components. Recovery requires configurable hysteresis and returns to the
pre-weakening progress state, preventing noise-driven oscillation.

Stop management is explicit simulation/signal policy. `STATIC` is the default.
`BREAKEVEN_AFTER_TP1` moves the monitored invalidation only after TP1 and applies the configured
buffer. `STRUCTURE_TRAILING` accepts only a caller-provided confirmed, lookahead-safe swing that
improves the stop. Targets remain static in this MVP. Every stop change stores old/new values,
policy, reason, and timestamp.

Read-only monitoring endpoints are `/signals/monitoring`, `/signals/{id}/live`, and
`/signals/{id}/events`; signal detail also includes monitoring state and history. Reproduce stored
events without waiting for a market using:

```bash
python scripts/replay_signal.py SIGNAL_UUID --events events.json
python scripts/replay_signal.py SIGNAL_UUID --demo
```

The replay path and live monitor share the same domain service. Neither has private exchange or
order capabilities. A process restart currently restores subscriptions from persistence; durable
distributed stream coordination and exchange-specific mark-price trigger policies remain future
work.

## Telegram and paper portfolios (Phase 5)

Telegram is the primary user interface. Create a bot with BotFather, put its token only in the local
`TELEGRAM_BOT_TOKEN` environment variable, set `TELEGRAM_ENABLED=true`, migrate the database, and
run:

```bash
alembic upgrade head
python scripts/run_telegram.py
```

The token is never required by tests, market-data services, or paper accounting. Supported commands
are `/start`, `/top`, `/active`, `/signals`, `/signal ID`, `/portfolio`, `/balance`, `/pnl`,
`/report`, `/report_week`, `/report_month`, `/stats`, `/settings`, `/reset_paper`, and `/help`.
Messages are split at Telegram's configured length limit.

On first `/start`, the bot explains its signal/paper-only scope, validates a starting virtual USDT
balance as a Decimal, asks for Spot/Futures preferences, and creates an independent paper account.
The default `BALANCED` paper simulation uses 10% of virtual equity per setup. `CONSERVATIVE` uses 5%
and `AGGRESSIVE` uses 20%; all are configurable assumptions, not real-money recommendations.
Futures default to 1x paper leverage and can be capped at 3x in configuration. Maximum concurrent
positions is configurable.

Paper entry is automatic by default for subscribed users when Phase 4 persists `ENTRY_CONFIRMED`.
BUY/LONG adds configured adverse entry slippage; SHORT subtracts it. Venue/market fee assumptions
live in configuration. Notional is `equity × profile exposure × paper leverage`, and quantity is
`notional / simulated fill`. Entry fees immediately reduce virtual cash. Futures equity is cash plus
P&L—it never adds derivative notional as an owned asset.

TP fills reuse the signal's normalized paper-exit plan (normally 30/30/40). Each partial fill stores
reference/fill price, Decimal quantity, gross P&L, fee, slippage, net P&L, and source monitoring
event. STOP closes the remainder with adverse simulated stop slippage. STOP_UPDATED synchronizes
the paper position with Phase 4 policy; it cannot invent a user-specific stop. Event uniqueness and
account/signal constraints prevent duplicate entry, TP, fee, or P&L processing after concurrency or
restart.

Account equity is virtual cash plus unrealized P&L. Meaningful event/mark-to-market snapshots update
peak equity, current drawdown, and maximum drawdown without persisting every tick. Reports provide
daily, weekly, monthly, all-time, and separate global-strategy statistics: net P&L, return, fees,
win rate, profit factor, expectancy, average R, holding time, drawdown, and breakdowns by exchange,
market, direction, BTC/ETH/altcoin, confidence bracket, timeframe, and setup type.

Notifications cover strong setups, entry/active, weakening/recovery, targets, stop changes/stops,
completion, and closure. Each user/event delivery has a durable key and status so restart cannot
resend history. Preference filters cover exchanges, markets, confidence, symbols/timeframes, signal
notifications, monitoring updates, reports, timezone, and paper profile. Scheduled report selection
uses each user's IANA timezone. `/reset_paper` requires confirmation, archives the old account, and
never deletes global signals.

Paper API endpoints under `/users/{id}` are read-only. If `TELEGRAM_ADMIN_IDS` is configured they
require a matching `X-Admin-ID` header; deployments should additionally place them behind normal
service authentication. They expose virtual portfolio, positions/open positions, and daily/weekly/
monthly/all-time reports only—never Telegram tokens, exchange wallets, or real balances.

Validate the complete deterministic 100-user event→paper→report→notification pipeline with:

```bash
python scripts/validate_phase5.py
```

Limitations: fills are simulations, liquidity impact is not modeled beyond configurable slippage,
scheduled report execution requires the host scheduler to call the timezone-aware selector, and no
real order, private exchange API, copy trading, deposit, withdrawal, or portfolio synchronization
exists.

## Realtime arbitrage research

Arbitrage is an independent domain under `app/arbitrage`; it does not alter directional signals.
Supported research routes are Binance↔Bybit Spot, equivalent USDT-linear perpetual spreads,
same-venue Spot/perpetual basis, and Binance↔Bybit funding comparisons. Reverse basis is
detection-only because this MVP does not model Spot borrowing.

Executable calculations consume asks for buys and bids for sells. Decimal VWAP is evaluated at
each configured notional and insufficient depth rejects the route. Expected net edge subtracts
configured venue fees, measured depth slippage, the safety buffer, and funding/basis effects where
relevant. Books preserve exchange and receive timestamps; stale, recovering, malformed, crossed,
or excessively skewed books are never compared.

Stable route fingerprints suppress per-tick duplicates. Opportunities must meet configurable
consecutive-update and persistence-duration rules before `QUALIFIED`. The explainable 0–100 score
covers edge, liquidity, persistence, freshness, timestamp alignment, stability and execution
quality. It is not a probability of profit.

Paper arbitrage uses separate two-leg positions at 1× notional semantics, capped by user and system
allocation settings. Decimal P&L combines buy, sell/short, fees, slippage and funding. Telegram
commands are `/arbitrage`, `/arbitrage_top`, `/arbitrage_active`, `/arbitrage_history`, and
`/arbitrage_stats`. Read-only APIs live under `/arbitrage`; user paper positions are available at
`/users/{id}/arbitrage/positions` under the existing admin protection.

Run the public-data validation and local VWAP benchmark with:

```bash
python scripts/validate_arbitrage.py
```

Detected arbitrage is not guaranteed executable profit. Network/exchange latency, liquidity,
fees, funding and price movement can eliminate an observed edge. No automatic execution or private
exchange access exists.

## Roadmap

1. ✅ Public REST/WebSocket exchange adapters and normalized market data.
2. ✅ Indicators and explainable market structure.
3. ✅ Phase 3A directional scoring and Phase 3B persistence, pre-entry lifecycle, deduplication,
   scanner orchestration, ranking, and read-only APIs.
4. ✅ Public-WebSocket realtime lifecycle monitoring, recovery, replay, and active signal APIs.
5. ✅ Independent Decimal paper portfolios with fees, slippage, partial fills, P&L, and drawdown.
6. ✅ Daily, weekly, monthly, all-time, segmented, and global strategy reports.
7. ✅ Telegram onboarding, primary commands, settings, reset, and durable notifications.
8. ✅ Realtime executable-book arbitrage research and two-leg paper simulation.
9. Live public-data forward testing without order execution.

## Safety disclaimer

This software is a market-research and simulated-performance tool. It is not financial advice and
does not promise guaranteed, risk-free, or profitable outcomes. All exposure, fills, balances, and
performance figures are simulated. A valid result may be **NO TRADE** when conditions are weak or
market data is stale.

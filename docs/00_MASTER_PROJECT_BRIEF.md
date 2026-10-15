# Futures Trading Intelligence Dashboard
## Master Project Context and Claude Development Instructions

> Original brief as written by the project owner. Later decisions (options focus, positional style, capital, instrument pool, automation levels) are recorded in `01_DECISIONS_AND_CONTEXT.md`, which takes precedence where they differ.

**Project owner:** Rahul Kumar Singh  
**Project type:** Private, cloud-hosted web application  
**Market:** Indian derivatives market  
**Initial instruments:** NIFTY futures and BANKNIFTY futures  
**Trading styles:** Intraday and positional analysis  
**Primary data provider to investigate:** Groww Trade API  
**Version 1 objective:** Market analysis, metrics, dashboard, explainable signals, and preparation for backtesting  
**Explicit restriction:** No live order placement or automated trade execution in Version 1.

---

# 1. Project Vision

I want to build my own website that acts as a **Futures Trading Intelligence Dashboard**.

The website should collect available market data from authorized sources, store it in a database, calculate technical and derivatives-market metrics, and present the information in a useful dashboard.

The objective is to help me evaluate whether market conditions support a bullish bias, bearish bias, or no trade.

The website should eventually help answer questions such as:

- What is the current market trend?
- Is the trend strengthening or weakening?
- Is price movement supported by volume and open interest?
- Are futures showing long buildup, short buildup, short covering, or long unwinding?
- Is the market trending or moving sideways?
- Is volatility unusually high?
- What do the selected technical indicators and available derivatives data suggest?
- Which factors support or contradict a potential trade?
- What conditions would invalidate a trading hypothesis?
- How have similar signals performed historically after transaction costs?
- When is the correct decision to stay out of the market?

This is not intended to be a generic stock-market news website or a broker's trading terminal.

It is a personal, data-driven market-analysis system that I can develop incrementally and eventually extend into an algorithmic trading project, subject to separate validation and approval.

**The system must help me make better-informed decisions, not promise profits or treat any indicator as a guaranteed prediction.**

# 2. My Background and How Claude Should Work With Me

I am a data analyst.

My strongest skill is SQL. I am a beginner in Python and want to improve while building this project.

Claude should act as a combination of:

- Senior full-stack software engineer
- Data engineer
- Quantitative research assistant
- Database architect
- Project manager
- Technical mentor

Explain the architecture and code clearly. Prefer simple, maintainable solutions over unnecessary complexity.

When providing commands, account for a Windows development environment unless I specify otherwise.

Do not overwhelm me with a giant codebase in one response. Build the application in milestones, run tests, explain what works, and identify what remains incomplete.

# 3. Scope and Requirements

## 3.1 Version 1 must include

1. NIFTY and BANKNIFTY underlying index information.
2. Actual NIFTY and BANKNIFTY futures contracts.
3. Contract expiry identification and expiry-aware analysis.
4. Intraday and positional analysis views.
5. Historical market-data storage.
6. Data ingestion from verified, authorized providers.
7. Technical-indicator calculation.
8. Futures price, volume, and open-interest analysis where available.
9. Data freshness and data-quality checks.
10. A browser-based dashboard.
11. Historical signal storage.
12. An architecture that supports future backtesting.
13. Cloud deployment after local testing.
14. Secure handling of provider credentials.

## 3.2 Explicitly excluded from Version 1

- Placing real trades.
- Automated broker order execution.
- An execution engine.
- Unverified or fabricated market data.
- Scraping or bypassing provider access restrictions.
- Treating an LLM response as a trading signal without deterministic, testable rules.
- Claims that the application can consistently predict the market or guarantee returns.

Future algorithmic execution is a separate phase. It must not be added without explicit approval, strategy validation, paper trading, security review, and compliance checks.

# 4. Initial Instruments and Trading Styles

## Instruments

Initially, cover:

- NIFTY index and its relevant futures contracts.
- BANKNIFTY index and its relevant futures contracts.

The instrument list must be based on actual available contracts and provider instrument identifiers.

Do not hard-code an expiry date permanently. Contract availability, lot size, and expiry information must be obtained from a verified source.

The application should distinguish the underlying index from each futures contract.

Never silently combine different futures expiries into one price series.

## Trading styles

### Intraday

Focus on shorter timeframes, subject to provider availability:

- 1-minute candles
- 5-minute candles
- 15-minute candles
- 1-hour candles

The actual supported intervals must be verified.

### Positional

Focus on broader market structure:

- Hourly candles
- Daily candles
- Multi-day returns
- Multi-timeframe trend
- Futures positioning and volatility
- Expiry and rollover considerations

A positional view should not rely exclusively on intraday noise.

# 5. Data Provider Strategy

## Primary provider to investigate: Groww Trade API

Official documentation starting point:

https://groww.in/trade-api/docs

Do not assume that every required field is available through Groww.

First verify the documentation, account plan, permissions, actual endpoint responses, request limits, pricing, and permitted data usage.

Create a provider capability matrix.

| Required capability | Status before verification |
|---|---|
| NIFTY index data | Not verified |
| BANKNIFTY index data | Not verified |
| Futures instrument identifiers | Not verified |
| Futures expiry information | Not verified |
| Historical OHLC candles | Not verified |
| Supported candle intervals | Not verified |
| Current market quotes | Not verified |
| Streaming market updates | Not verified |
| Futures volume | Not verified |
| Futures open interest | Not verified |
| Open-interest changes | Not verified |
| Options chain | Not verified |
| Options OI and IV | Not verified |
| Historical retention and storage permissions | Not verified |
| API pricing and rate limits | Not verified |

Use these statuses consistently:

- `SUPPORTED`
- `UNSUPPORTED`
- `NOT_VERIFIED`
- `REQUIRES_PAID_PLAN`
- `SOURCE_UNAVAILABLE`

If a field is unavailable from Groww, identify the gap and investigate an authorized alternative.

Do not fabricate a value to make the dashboard look complete.

Do not assume that a broker API provides all historical, participant-positioning, options, or market-context data.

### Security requirement

Never ask me to paste API tokens, passwords, or secret keys into a Claude conversation or a Markdown document.

Store secrets in environment variables or an appropriate secret manager. Use placeholder names in `.env.example`.

# 6. Agreed Metric Framework

The metrics should be implemented in phases rather than all at once.

The following is the consolidated working catalog based on our discussion. If I provide the original workbook or the complete metric list, reconcile this catalog against it before claiming that every previously discussed metric has been included.

## 6.1 Data-quality metrics and checks

These are foundational and should be implemented before trading signals.

- Last data-update timestamp
- Source timestamp
- Data ingestion timestamp
- Data freshness and lag
- Missing candle detection
- Duplicate candle detection
- Invalid OHLC detection
- Missing volume detection
- Missing open-interest detection
- Missing interval or timestamp gaps
- Instrument identifier validation
- Expiry mapping validation
- Source availability status
- Metric calculation status

Missing values must remain missing. Do not replace missing OI, volume, or indicator values with zero.

A stale or invalid data feed should prevent the application from presenting an apparently valid trading signal.

## 6.2 Trend metrics

Priority: Core Version 1.

- EMA 20
- EMA 50
- EMA 200
- SMA 20/50/200, if useful
- Price relative to each moving average
- EMA alignment
- Short-term trend
- Medium-term trend
- Long-term trend
- Multi-timeframe trend agreement
- Trend direction
- Trend strength
- Swing highs and swing lows
- Support and resistance
- Breakout and breakdown conditions

Every trend metric must define its timeframe and formula.

Do not use incomplete future candles in historical analysis.

## 6.3 Momentum metrics

Priority: Core Version 1.

- RSI
- ADX
- Rate of Change (ROC)
- Returns over 5 minutes
- Returns over 15 minutes
- Returns over 1 hour
- Returns over 5 trading days
- Returns over 20 trading days
- Momentum direction
- Momentum strength
- Momentum divergence, if implemented using a defined rule
- MACD, as an optional secondary indicator

ADX measures trend strength; it does not independently establish bullish or bearish direction.

Avoid adding many highly correlated indicators simply to increase the number of metrics.

## 6.4 Price and volatility metrics

Priority: Core Version 1.

- OHLC price
- Candle range
- VWAP
- ATR
- ATR percentage
- Relative volume
- Intraday high
- Intraday low
- Distance from intraday high/low
- Previous-session close
- Gap from previous close
- Volatility regime
- Price extension from VWAP
- Price extension from moving averages
- Support/resistance distance
- Breakout confirmation
- Potential false-breakout conditions

VWAP requires suitable volume data. If the provider does not supply the necessary inputs, mark VWAP unavailable or use a clearly documented alternative source.

Relative volume must use a sensible comparison baseline, preferably comparable bars or times of day.

## 6.5 Futures positioning metrics

Priority: Core Version 1 where supported.

- Futures price
- Futures OHLC
- Futures volume
- Open interest (OI)
- Absolute OI change
- Percentage OI change
- Price change
- Price/OI buildup classification
- Days to expiry
- Front-month futures basis
- Futures basis percentage
- Front/next-contract spread
- Rollover measures, when sufficient data exists

### Price/OI classification

Use the following as a heuristic:

| Price movement | OI movement | Interpretation |
|---|---|---|
| Rising | Rising | Long buildup |
| Falling | Rising | Short buildup |
| Rising | Falling | Short covering |
| Falling | Falling | Long unwinding |

These classifications are interpretations, not proof of what individual traders are doing.

Define exactly which previous observation is used for OI change. Do not compare inconsistent timestamps.

### Futures basis

Basis:

`Futures Price - Underlying Index Price`

Basis percentage:

`(Futures Price - Underlying Index Price) / Underlying Index Price * 100`

Use comparable timestamps and appropriate contract expiry information.

Fair-value and carry calculations should be treated as advanced contextual metrics. Their assumptions must be documented, and they must not become standalone trade signals.

## 6.6 Options metrics and broader market context

Priority: Conditional on verified data availability.

- Put-call ratio based on OI
- Put-call ratio based on volume
- Call OI change
- Put OI change
- ATM implied volatility
- Options chain by strike and expiry
- India VIX
- Market breadth
- Advance/decline data
- FII positioning
- Participant-wise positioning
- Relevant corporate or economic events
- Market-session status
- F&O ban status
- Other relevant market risk flags

These metrics should be implemented only after the source, data frequency, timestamp, and usage rights are verified.

For example, daily participant-positioning data must not be displayed as real-time positioning.

PCR alone must never determine a trading direction.

## 6.7 Relative-strength metrics

- NIFTY versus BANKNIFTY performance
- Relative performance over selected periods
- Relative momentum
- Benchmark-relative returns
- Sector-relative strength, if reliable sector data becomes available

The comparison period and formula must be explicitly defined.

## 6.8 Signal and risk metrics

- Rule score
- Directional bias
- Supporting factors
- Contradicting factors
- Data-quality flags
- Volatility risk
- Expiry risk
- Rollover risk
- Entry trigger, when defined by a tested strategy
- Invalidation level, when defined by a tested strategy
- Target levels, when defined by a tested strategy
- Reward-to-risk ratio
- Rule version
- Signal-generation timestamp
- Source-data timestamp
- Historical outcome
- Estimated costs and slippage
- Maximum favorable excursion
- Maximum adverse excursion

The dashboard should use three primary outputs:

- `LONG BIAS`
- `SHORT BIAS`
- `NO TRADE`

A numerical rule score is not a calibrated probability.

Only display confidence probabilities if they are subsequently calibrated and validated on appropriate out-of-sample data.

# 7. Metric Implementation Priorities

## Phase A — Reliable data pipeline

Before calculating dozens of indicators, implement:

1. Instrument and expiry mapping.
2. Historical candles, where supported.
3. Current snapshots or streaming data, where supported.
4. Data-quality validation.
5. PostgreSQL storage.
6. Read-only backend API.
7. A basic dashboard.
8. Last-update and source-status indicators.

## Phase B — Core metrics

Implement:

- EMA 20/50/200
- RSI
- ADX
- ROC
- Returns
- VWAP
- ATR and ATR%
- Relative volume
- OI and OI change, where available
- Price/OI classification
- Futures basis, where appropriate

## Phase C — Market context

Add options metrics, India VIX, breadth, participant positioning, and events only when verified data sources are available.

## Phase D — Explainable signals

Develop transparent rules with explicit thresholds, invalidation conditions, risk controls, and a `NO TRADE` state.

## Phase E — Backtesting

Validate the strategy with realistic transaction costs, slippage, correct contract-expiry handling, and no look-ahead bias.

# 8. Website Design

Build a clean, professional dashboard that is easy to read during market hours.

It should prioritize useful information over decorative complexity.

## Page 1: Market Overview

Display:

- NIFTY index status
- BANKNIFTY index status
- Selected futures contracts
- Expiry dates
- Latest available prices
- Price change and percentage change
- Market trend summary
- Volatility status
- Data freshness
- Data-quality status
- Current signal status, once implemented

## Page 2: Futures Analysis

Allow me to select:

- NIFTY or BANKNIFTY
- Actual futures contract and expiry
- Intraday or positional view
- Supported timeframe

Display:

- Candlestick chart
- Volume
- Open interest, when available
- EMA overlays
- VWAP
- RSI and ADX panels
- ATR and volatility information
- Price/OI classification
- Basis information, when available
- Support and resistance
- Metric calculation timestamps

## Page 3: Options and Market Context

Display verified information such as:

- Options chain
- PCR
- Call/put OI changes
- ATM IV
- India VIX
- Market breadth
- Participant positioning
- Event and market-status flags

Hide or clearly label unavailable fields.

## Page 4: Signal Explanation

For every generated signal, show:

- Directional bias
- Rule score
- Signal timestamp
- Instrument and expiry
- Timeframe
- Supporting factors
- Contradicting factors
- Risk flags
- Data-quality status
- Rule version
- Entry trigger and invalidation conditions, if defined
- Targets and reward-to-risk ratio, if defined

Explain why a signal was generated in understandable language.

## Page 5: Signal History and Performance

Display:

- Previous signals
- Signal-generation time
- Instrument and expiry
- Directional bias
- Historical outcome
- Evaluation horizon
- Gross return
- Estimated costs
- Net return
- Maximum favorable excursion
- Maximum adverse excursion
- Backtesting assumptions

Do not mix hypothetical backtest results with actual live trading results.

Clearly label simulations and sample data.

# 9. Recommended Technology Stack

Use this stack unless a material technical reason justifies a change.

| Component | Technology | Purpose |
|---|---|---|
| Frontend | Next.js | Website |
| Frontend language | TypeScript | Maintainable UI logic |
| Styling | Tailwind CSS | Responsive dashboard |
| Charts | Lightweight Charts or suitable alternative | Market charts |
| Backend | Python + FastAPI | API and business logic |
| Data processing | Pandas + NumPy | Metric calculations |
| Database | PostgreSQL | Historical and normalized data |
| Database migrations | Alembic or equivalent | Controlled schema changes |
| Testing | pytest and frontend checks | Reliability |
| Version control | Git | Source-code management |
| Frontend hosting | Vercel candidate | Website deployment |
| Backend hosting | Python-capable cloud host | API and data collector |
| Database hosting | Managed PostgreSQL candidate | Persistent storage |

Verify current hosting prices, regional availability, background-worker support, and free-tier restrictions before committing to a provider.

A serverless frontend is not a substitute for a persistent market-data collector.

# 10. Application Architecture

The proposed data flow is:

```text
Groww Trade API or another authorized source
                    |
                    v
             Python Collector
                    |
                    v
        Data Validation and Normalization
                    |
                    v
                PostgreSQL
                    |
                    v
            Metric Calculation
                    |
                    v
        Versioned Signal Rules Engine
                    |
                    v
             FastAPI Read API
                    |
                    v
          Next.js Web Dashboard
                    |
                    v
            User in Browser
```

Keep each component separate so that changing the market-data provider does not require rebuilding the entire application.

The provider integration should be isolated behind an adapter.

If a provider does not support a required feature, the adapter should return a clear unavailable status rather than fabricate a result.

# 11. Proposed Database Design

The following schema is provisional. Finalize it after inspecting real provider payloads.

## 11.1 `instruments`

Purpose: Store the underlying indices and actual tradable contracts.

Suggested fields: `id`, `symbol`, `instrument_type`, `exchange`, `expiry_date`, `strike_price`, `option_type`, `lot_size`, `provider_instrument_id`, `is_active`, `created_at`, `updated_at`

Instrument types may include `INDEX`, `FUTURE`, and `OPTION`.

## 11.2 `market_candles`

Purpose: Store historical OHLC data.

Suggested fields: `instrument_id`, `timeframe`, `candle_time`, `open`, `high`, `low`, `close`, `volume`, `open_interest`, `source`, `received_at`, `quality_status`

Use a unique constraint on `(instrument_id, timeframe, candle_time)`.

Use suitable numeric precision for prices and volumes. Keep optional fields nullable.

## 11.3 `market_snapshots`

Purpose: Store observed market snapshots where available.

Suggested fields: `id`, `instrument_id`, `observed_at`, `last_price`, `bid_price`, `ask_price`, `day_open`, `day_high`, `day_low`, `previous_close`, `volume`, `open_interest`, `oi_change`, `source`, `quality_status`, `is_stale`

Only include fields the source actually provides.

## 11.4 `market_metrics`

Purpose: Store calculated indicators.

Suggested fields: `id`, `instrument_id`, `calculated_at`, `timeframe`, `metric_name`, `metric_value`, `status`, `reason`, `source_data_timestamp`, `calculation_version`

The status and reason fields should explain missing inputs, insufficient history, or calculation errors.

## 11.5 `market_context`

Purpose: Store broader market information.

Suggested fields: `id`, `context_name`, `value`, `observed_at`, `source`, `quality_status`, `metadata`

Examples include India VIX, market breadth, participant positioning, and event flags, if available.

## 11.6 `trading_signals`

Purpose: Store generated analysis outputs.

Suggested fields: `id`, `instrument_id`, `generated_at`, `trading_style`, `timeframe`, `direction`, `rule_score`, `confidence_probability`, `entry_trigger`, `invalidation_price`, `target_1`, `target_2`, `reward_risk_ratio`, `supporting_factors`, `contradicting_factors`, `risk_flags`, `rule_version`, `data_quality_status`

Optional values should remain null when they are not calculated or supported.

## 11.7 `signal_outcomes`

Purpose: Evaluate historical signals.

Suggested fields: `id`, `signal_id`, `evaluation_horizon`, `reference_price`, `future_price`, `gross_return_pct`, `estimated_cost_pct`, `net_return_pct`, `maximum_favorable_excursion`, `maximum_adverse_excursion`, `evaluated_at`, `methodology_version`

Use appropriate foreign keys, indexes, migrations, and database constraints.

# 12. Backend API Design

Proposed read-only endpoints:

```text
GET /health
GET /api/instruments
GET /api/market/overview
GET /api/candles
GET /api/metrics
GET /api/signals
GET /api/signals/{id}
GET /api/data-quality
```

Possible candle query parameters: `instrument_id`, `timeframe`, `start`, `end`

Possible signal query parameters: `instrument_id`, `style`

Validate input parameters and impose reasonable response limits.

Do not expose credentials, database connection strings, or sensitive internal exceptions.

**No order-placement endpoints are allowed in Version 1.**

# 13. Data Engineering Requirements

These are mandatory.

1. Store source data before calculating derived metrics.
2. Preserve provider timestamps and ingestion timestamps.
3. Store timestamps consistently, preferably in UTC.
4. Display market times in Asia/Kolkata.
5. Respect Indian market sessions and holidays.
6. Handle contract expiry and rollover explicitly.
7. Use idempotent ingestion and database upserts.
8. Prevent duplicate candles.
9. Detect missing intervals and invalid OHLC values.
10. Do not interpret missing values as zero.
11. Version all metric and signal calculations.
12. Record the source-data timestamp used for each signal.
13. Do not use information that would not have been available at the historical signal timestamp.
14. Clearly distinguish real market data from mock/test data.
15. Maintain logs for ingestion failures and calculation errors.
16. Keep the provider adapter separate from core business logic.

# 14. Signal Generation Strategy

Begin with transparent, deterministic rules.

Do not start with machine learning or an LLM deciding trades.

The initial signal engine should combine a small number of meaningful factors, for example:

- Trend
- Momentum
- Futures positioning
- Price structure and volatility
- Market context
- Risk controls

Avoid double-counting highly correlated indicators.

Every rule must have a documented formula, threshold, timeframe, and test.

The signal engine must be able to return `NO TRADE`.

This should be the default when:

- Market data is stale.
- Required inputs are missing.
- Instrument or expiry mapping is uncertain.
- Indicators do not have sufficient history.
- Conditions are contradictory.
- Risk limits are breached.
- The strategy's entry criteria are not satisfied.

Do not generate targets or entry levels without a clearly defined and tested strategy.

# 15. Backtesting and Validation

Backtesting is a later milestone, not the first implementation task.

Before trusting any strategy:

- Avoid look-ahead bias.
- Avoid data leakage.
- Handle futures expiry and rollovers correctly.
- Use realistic entry and exit assumptions.
- Include brokerage, exchange charges, taxes and other applicable costs.
- Include realistic slippage.
- Separate development data from validation data.
- Consider out-of-sample and walk-forward validation.
- Record the number of observations and trades.
- Evaluate drawdown and the distribution of returns.
- Track maximum favorable and adverse excursion.
- Document strategy limitations.

Do not judge a strategy solely by its win rate.

Do not describe backtested profitability as proof of future profitability.

# 16. Deployment and Security

Develop locally first.

Recommended sequence:

1. Create a Git repository.
2. Build the backend.
3. Configure PostgreSQL.
4. Test data ingestion using verified endpoints or clearly labeled sample fixtures.
5. Build the dashboard.
6. Run tests and type checks.
7. Deploy the backend and database.
8. Deploy the frontend.
9. Configure secrets and authentication.
10. Verify the deployed application end to end.

Security requirements:

- Keep `.env` in `.gitignore`.
- Commit only `.env.example` with placeholders.
- Keep API keys on the server.
- Use HTTPS.
- Restrict database access.
- Configure narrow CORS rules.
- Add authentication because the dashboard is private.
- Set up database backups and test restoration.
- Use least-privilege credentials.
- Review provider data-use terms before storing or displaying market data.

Do not expose the dashboard publicly without appropriate access controls.

# 17. Proposed Repository Structure

```text
tradeX/
|-- README.md
|-- CLAUDE.md
|-- .gitignore
|-- .env.example
|-- docker-compose.yml
|-- docs/
|-- backend/
|   |-- pyproject.toml
|   |-- app/
|   |   |-- main.py
|   |   |-- core/ (config.py, logging.py)
|   |   |-- api/routes/
|   |   |-- providers/ (base.py, groww.py)
|   |   |-- collectors/
|   |   |-- db/
|   |   |-- services/ (data_quality.py, metrics.py, signals.py, backtesting.py)
|   |-- tests/
|-- frontend/
|   |-- package.json
|   |-- src/ (app/, components/, features/, lib/)
|-- scripts/
    |-- verify_provider_capabilities.py
    |-- backfill_candles.py
```

This is a proposed structure, not a requirement to create every file immediately. Build only what is needed for the current milestone.

# 18. Development Roadmap

## Milestone 0: Verify requirements and data access
- Review current official Groww documentation.
- Confirm available API access and account plan.
- Verify instrument and expiry information.
- Verify historical candle access.
- Verify current quotes or streaming access.
- Verify volume and OI availability.
- Record rate limits, costs, and usage restrictions.
- Inspect sanitized API responses.
- Create the provider capability matrix.

**Completion condition:** The project does not depend on guessed API endpoints or unverified fields.

## Milestone 1: Working end-to-end MVP
- Create the project repository.
- Configure the local development environment.
- Create PostgreSQL tables and migrations.
- Implement a provider adapter.
- Ingest a small, verified sample of market data.
- Validate and store candles.
- Implement read-only FastAPI endpoints.
- Build a basic Next.js dashboard.
- Show NIFTY and BANKNIFTY information where supported.
- Add instrument, expiry, and timeframe selection.
- Display a price chart.
- Show last-update and data-quality indicators.
- Add tests and setup documentation.

**Completion condition:** Real supported data flows from the provider to PostgreSQL, through the backend, and into the browser. Repeated ingestion does not create duplicate candles.

If real provider access is not yet available, use clearly labeled test fixtures to build the pipeline, but do not represent those fixtures as live data.

## Milestone 2: Core metrics
Implement and test: EMA 20/50/200, RSI, ADX, ROC, Returns, VWAP, ATR and ATR%, Relative volume, OI and OI changes where supported, Price/OI classification, Futures basis where appropriate.

Every metric needs a definition, unit tests, and missing-data behavior.

## Milestone 3: Options and market context
Add options and wider market context only after verifying the data source and its permissions.

## Milestone 4: Explainable signals
Write the strategy specification before implementing signal rules. Include entry conditions, exit assumptions, invalidation conditions, risk limits, no-trade rules, and rule versioning.

## Milestone 5: Backtesting
Build the evaluation framework with realistic costs, slippage, expiry handling, and validation.

## Milestone 6: Paper monitoring
Monitor signals without placing trades. Track data reliability and signal outcomes.

## Milestone 7: Future execution project
This is outside Version 1 and requires a separate explicit decision.

# 19. Acceptance Criteria for Version 1

- [ ] The project runs locally using documented commands.
- [ ] The provider capability matrix is completed as far as evidence permits.
- [ ] Instrument identifiers and expiry mapping are verified.
- [ ] At least one supported historical candle source is integrated.
- [ ] Current snapshots or streaming are integrated only if supported.
- [ ] Market data is validated and persisted in PostgreSQL.
- [ ] Repeated ingestion does not duplicate candles.
- [ ] FastAPI exposes read-only market-data endpoints.
- [ ] The dashboard displays available NIFTY/BANKNIFTY information.
- [ ] The user can select an instrument, expiry, and supported timeframe.
- [ ] Data freshness and quality are visible.
- [ ] Unsupported metrics are clearly labeled.
- [ ] Secrets are not committed to Git.
- [ ] Tests and setup instructions work.
- [ ] No broker order-execution functionality exists.

# 20. Instructions to Claude

Claude, read this entire document before starting implementation. Treat it as the master project brief.

Your first task is not to build the entire application in one response. Instead:

1. Summarize your understanding of the project.
2. Identify any important missing requirements.
3. Review the current official Groww Trade API documentation and account requirements.
4. Build a capability matrix distinguishing verified support from assumptions.
5. Propose the smallest runnable Milestone 1.
6. Inspect any existing repository before changing it.
7. Implement the first milestone incrementally.
8. Run relevant tests, lint checks, and type checks.
9. Explain how to run the project locally on Windows.
10. Report what is working, what is untested, what remains blocked, and what the next step is.

### Development rules

- Do not invent endpoints, data fields, prices, or provider permissions.
- Do not fabricate market data.
- Do not claim that code or deployment works unless it has actually been tested.
- Do not request API secrets in chat.
- Do not commit credentials.
- Do not implement order execution.
- Do not expose the private dashboard without suitable security.
- Do not use an LLM as the sole trading decision-maker.
- Do not skip tests or data-quality validation.
- Explain major architecture changes before making them.
- Ask before destructive changes or actions that could incur cloud costs.
- Keep the code maintainable and update documentation as the project evolves.

**The first objective is a small, working end-to-end application—not a large collection of unfinished features.**

Build the foundation correctly, then expand it.

---

# Final Project Principle

The quality of this website will depend on reliable market data, correct calculations, transparent rules, sound historical testing, and disciplined risk management—not on how many indicators it displays.

Prioritize data quality first, core metrics second, explainable signals third, and backtesting before considering any future execution system.

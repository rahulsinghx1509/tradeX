# Running TradeX on Windows

This gets the whole Milestone 1 pipeline running on your laptop:
data source → PostgreSQL → API → dashboard in your browser.

You will open **three PowerShell windows**: one for the API, one for the
dashboard, and one for running commands. Commands below are for PowerShell.

---

## 1. Install the tools (once)

| Tool | Version | Where |
|---|---|---|
| Git | any recent | https://git-scm.com/download/win |
| Python | 3.12 or 3.13 | https://www.python.org/downloads/ — tick **"Add python.exe to PATH"** |
| Node.js | 22 LTS | https://nodejs.org/ |
| PostgreSQL | 16 | https://www.postgresql.org/download/windows/ (remember the password you set for `postgres`) |

Prefer Docker? Install Docker Desktop instead of PostgreSQL and run
`docker compose up -d` in the repo folder. It creates the `tradex` database for you, so skip step 3.

Check they work:

```powershell
git --version
python --version
node --version
```

## 2. Get the code

```powershell
cd $HOME\Documents
git clone https://github.com/rahulsinghx1509/tradeX.git
cd tradeX
```

## 3. Create the database (skip if you used Docker)

Open **SQL Shell (psql)** from the Start menu, log in as `postgres`, and run:

```sql
CREATE USER tradex WITH PASSWORD 'change_me';
CREATE DATABASE tradex OWNER tradex;
```

Use a better password if you like, and put the same one in `.env` in the next step.

## 4. Create your `.env` file

```powershell
copy .env.example .env
notepad .env
```

For now leave `DATA_PROVIDER=sample`. That loads **synthetic sample data** (clearly
labelled everywhere) so you can see the whole system working before Groww is connected.

## 5. Set up the backend (Python)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
alembic upgrade head
```

- `python -m venv .venv` makes a private Python just for this project.
- `Activate.ps1` switches to it. You will see `(.venv)` in the prompt. Do this in every new window.
  If PowerShell refuses with "running scripts is disabled", run this once:
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
- `alembic upgrade head` creates the tables (`instruments`, `market_candles`, …).

Run the tests (should say all passed):

```powershell
python -m pytest
```

## 6. Load data

From the **repo root** (`cd ..` first), with the venv active:

```powershell
python scripts\backfill_candles.py
```

Run it again: the candle counts stay the same. That proves repeated loads never
create duplicates.

## 7. Start the API (window 1)

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs to see and try every endpoint.

## 8. Start the dashboard (window 2)

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:3000. You should see NIFTY and BANKNIFTY cards, a yellow
**SAMPLE DATA** banner, a candlestick chart with instrument / expiry / timeframe
selectors, freshness badges and the data-quality table.

---

## Switching to real Groww data

1. Get your keys at https://groww.in/trade-api/api-keys.
2. In `.env` fill in `GROWW_API_KEY` + `GROWW_API_SECRET` (or the TOTP pair), and set
   `DATA_PROVIDER=groww`. Keep the keys only in `.env`; never paste them into a chat.
   The Groww key can place orders, so treat it like a bank password. TradeX itself never
   calls any order method.
3. Check what your account really provides (this is Milestone 0):
   ```powershell
   python scripts\verify_provider_capabilities.py
   ```
   It writes `reports\provider_capability_report.md` (counts and field names only,
   no prices). Share that file so we can confirm or fix the assumptions in
   `backend/app/providers/groww.py`. The most important line is
   **"Expired contracts history"**: it decides how options strategies can be backtested.
4. Load real data: `python scripts\backfill_candles.py`. Real rows have
   `provider = 'groww'` and `is_sample = false`. The API and dashboard only show the
   provider named in `DATA_PROVIDER`, so sample rows stay in the database but are hidden
   and the SAMPLE banner goes away. To remove them for good:
   `DELETE` the sample rows, or drop and recreate the database and run `alembic upgrade head`.

## Connecting Telegram

1. In Telegram, message **@BotFather**, send `/newbot`, follow the steps. Put the token
   it gives you in `.env` as `TELEGRAM_BOT_TOKEN`.
2. Send your new bot any message ("hi").
3. `python scripts\telegram_chat_id.py` prints your chat id. Put it in `.env` as `TELEGRAM_CHAT_ID`.
4. `python scripts\send_test_alert.py` sends a test message to your phone.

## Running the collector (what the server will do all day)

```powershell
python scripts\run_collector.py --once   # one cycle, then exits
python scripts\run_collector.py          # keeps running; Ctrl+C to stop
```

During market hours it stores quotes and the latest 1-hour candles every 5 minutes;
after the close it loads the daily candle and sends an end-of-day Telegram message.
On a laptop it stops when the laptop sleeps, which is why the always-on server is needed later.

---

## Looking at the data with SQL

Connect with psql, pgAdmin or DBeaver (`localhost:5432`, database `tradex`, user `tradex`).

```sql
-- How many candles per instrument and timeframe, and the date range
SELECT i.provider_symbol, c.timeframe, COUNT(*) AS candles,
       MIN(c.candle_time AT TIME ZONE 'Asia/Kolkata') AS first_ist,
       MAX(c.candle_time AT TIME ZONE 'Asia/Kolkata') AS last_ist
FROM market_candles c
JOIN instruments i ON i.id = c.instrument_id
GROUP BY 1, 2
ORDER BY 1, 2;

-- Prove there are no duplicates (both numbers must match)
SELECT COUNT(*) AS total_rows,
       COUNT(DISTINCT (instrument_id, timeframe, candle_time)) AS unique_keys
FROM market_candles;

-- Any data-quality problems in the last day?
SELECT checked_at AT TIME ZONE 'Asia/Kolkata' AS checked_ist, instrument_id, timeframe,
       check_name, status, details
FROM data_quality_log
WHERE status <> 'PASS' AND checked_at > NOW() - INTERVAL '1 day'
ORDER BY checked_at DESC;

-- Failed loads
SELECT * FROM ingestion_runs WHERE status = 'FAILED' ORDER BY id DESC LIMIT 20;
```

Times are stored in UTC; `AT TIME ZONE 'Asia/Kolkata'` shows them in IST.

## Troubleshooting

| Problem | Fix |
|---|---|
| `connection refused` on port 5432 | PostgreSQL is not running. Start it from Services, or `docker compose up -d`. |
| `password authentication failed` | The password in `DATABASE_URL` in `.env` doesn't match the database user. |
| Dashboard says it can't reach the API | Start the API (step 7). It must be on port 8000. |
| Lots of "missing candles" warnings on certain dates | Those are probably NSE holidays. Add them to `backend/app/data/nse_holidays.csv`. |
| `ZoneInfoNotFoundError: Asia/Kolkata` | `pip install -r requirements.txt` again (it installs `tzdata`). |

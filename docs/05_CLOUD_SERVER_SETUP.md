# Running TradeX on the cloud server (AWS Lightsail, Mumbai)

This guide takes a brand-new Lightsail server to a running TradeX: database, API,
collector, Telegram alerts and dashboard, all 24/7. You only need a web browser.
Everything is typed into the **browser terminal** that Lightsail gives you.

Total time: about 30–45 minutes, once.

## How it fits together

```
 Your phone / any device ──Tailscale (private, encrypted)──► tradex server (Lightsail Mumbai)
                                                              ├─ caddy      front door on port 80
                                                              ├─ dashboard  Next.js
                                                              ├─ api        FastAPI (read-only)
                                                              ├─ collector  market-hours loop + Telegram
                                                              └─ db         PostgreSQL (not reachable from outside)
 GitHub (main branch) ◄── the server checks every 5 min and deploys new merges by itself
```

- **Secrets** (Groww, Telegram, database password) live only in `~/tradex/.env` on the server,
  readable only by your user. They are never in GitHub and never in chat.
- **Claude never logs in to the server.** Claude opens pull requests; when you merge one,
  the server picks it up within 5 minutes.
- **No orders.** Version 1 has no order placement code at all.

## Part A — In the Lightsail console

### A1. Create the server

1. Lightsail → **Create instance**.
2. Region: **Mumbai (ap-south-1)**, any zone.
3. Platform **Linux/Unix** → **OS Only** → **Ubuntu 24.04 LTS**.
4. Plan: **$7 / month (1 GB RAM, 2 vCPU, 40 GB SSD)**, about ₹785 a month with GST.
   TradeX uses about 350 MB when running, and the setup adds swap so updates fit too.
   New AWS accounts are often blocked from the $12 (2 GB) plan at first
   ("your account can not create an instance using this Lightsail plan size"). That's fine;
   see *Moving to a bigger plan later* near the end of this guide.
5. Name: `tradex-server` → **Create instance**. Wait until it says *Running*.

### A2. Give it a static IP

1. Lightsail → **Networking** → **Create static IP**.
2. Region Mumbai, attach to `tradex-server`, name it `tradex-ip` → **Create**.
3. Write the address down (looks like `13.233.x.x`). It isn't a secret; send it to Claude.

Keep it attached. A static IP that is *not* attached to a running server is billed.

### A3. Close the public web port

TradeX's dashboard is reached through Tailscale, so the internet doesn't need port 80.

1. Open `tradex-server` → **Networking** tab → **IPv4 Firewall**.
2. Delete the **HTTP (80)** rule. Leave **SSH (22)** (the browser terminal needs it).
3. Do the same under **IPv6 Firewall** if an HTTP rule is listed there.

### A4. (Optional) Automatic snapshots

`tradex-server` → **Snapshots** → turn on **Automatic snapshots**. This copies the whole
server daily and costs extra (a few hundred rupees a month at most; check the price shown before turning it on). TradeX also backs up its own database every evening (Part D), so this is extra safety.

## Part B — In Groww

1. Groww → **Trade API → API Keys** → **Add static IP**.
2. Enter the static IP from A2 as the primary IP → **Update**.

For a 24/7 server, the **TOTP** login is better than *API key + secret*, because the
key + secret option needs your approval in Groww once a day. Either works with TradeX.

## Part C — Telegram bot

1. In Telegram, open **@BotFather** → send `/newbot` → pick a name → copy the **token**.
2. Open your new bot and send it any message (e.g. `hi`). Bots can only message people who wrote to them first.

You'll paste the token into the server in step E2.

## Part D — Set up the server

Open `tradex-server` in Lightsail and click **Connect using SSH**. A black terminal opens in your browser.
Copy each block, paste it with **Ctrl+V** (if that doesn't work, use the clipboard icon at the bottom-right of the terminal), and press Enter.

### D1. Give the server read-only access to the private GitHub repo

```bash
ssh-keygen -t ed25519 -N "" -C "tradex-server" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

Copy the line it prints (starts with `ssh-ed25519`). Then on GitHub:
**tradex repo → Settings → Deploy keys → Add deploy key** → title `tradex-server`,
paste the line, leave **Allow write access** unticked → **Add key**.

### D2. Download TradeX and run the setup script

```bash
ssh-keyscan github.com >> ~/.ssh/known_hosts
git clone git@github.com:rahulsinghx1509/tradex.git ~/tradex
cd ~/tradex && bash deploy/setup_server.sh
```

This takes a few minutes. It installs Docker, adds swap memory, creates `~/tradex/.env`
with a random database password, schedules auto-updates and the nightly backup, and installs Tailscale.

### D3. Connect Tailscale

```bash
sudo tailscale up --hostname tradex
```

It prints a link. Open it, sign in (Google login is fine) and approve the server.
Then install the **Tailscale** app on your phone and laptop and sign in with the same account.

### D4. Log out and back in

Type `exit`, close the terminal, and click **Connect using SSH** again.
(This lets Docker run without `sudo`.)

## Part E — Start TradeX

### E1. First start with sample data

```bash
~/tradex/deploy/tradex.sh start
```

The first build takes 5–10 minutes. When it finishes:

```bash
~/tradex/deploy/tradex.sh status
~/tradex/deploy/tradex.sh backfill --days-1d 30 --days-1h 10
```

On your phone (with Tailscale on) open **http://tradex** (if that name doesn't work, use the address that `tailscale ip -4` prints on the server, e.g. `http://100.x.y.z`). You should see the dashboard
with a **SAMPLE DATA** banner. Sample data is synthetic and clearly labelled.

### E2. Add your keys

```bash
nano ~/tradex/.env
```

Use the arrow keys to move. Fill in:

- `GROWW_TOTP_TOKEN` and `GROWW_TOTP_SECRET` (or `GROWW_API_KEY` and `GROWW_API_SECRET`)
- `TELEGRAM_BOT_TOKEN`

Save with **Ctrl+O**, **Enter**, then exit with **Ctrl+X**. Leave `DATA_PROVIDER=sample` for now.

### E3. Telegram

```bash
~/tradex/deploy/tradex.sh chat-id
```

Copy the number it prints into `TELEGRAM_CHAT_ID` in `.env` (as in E2), then:

```bash
~/tradex/deploy/tradex.sh test-alert
```

You should get a Telegram message.

### E4. Milestone 0: check what Groww really provides

```bash
~/tradex/deploy/tradex.sh verify
cat ~/tradex/reports/provider_capability_report.md
```

This only reads data; it never places orders. Paste the report's table to Claude
(it lists what worked and what didn't; glance over it and leave out anything that looks like a key), and Claude will fix anything the Groww adapter assumed wrongly.

### E5. Switch to real data (after Claude confirms the report)

In `.env` set `DATA_PROVIDER=groww`, then:

```bash
~/tradex/deploy/tradex.sh start
~/tradex/deploy/tradex.sh backfill
```

The dashboard banner disappears once only real data is shown.

## Everyday commands

| Command | What it does |
|---|---|
| `~/tradex/deploy/tradex.sh status` | What's running |
| `~/tradex/deploy/tradex.sh study` | Backtest the four core readings; report in `~/tradex/reports/signal_study.md` |
| `~/tradex/deploy/tradex.sh variants` | Compare long-only variants on the development years; report in `~/tradex/reports/long_variants.md` |
| `~/tradex/deploy/tradex.sh final` | One-time final test of the trailing-stop rules on the held-back years; report in `~/tradex/reports/final_test.md` |
| `~/tradex/deploy/tradex.sh fo-history` | Load NSE's free daily F&O files (futures, and options near the money) from 2020 into the database; 1-2 hours, safe to stop and re-run. New days then load by themselves every evening from 19:00 IST |
| `~/tradex/deploy/tradex.sh option-metrics` | Rebuild option metrics (ATM IV, skew, PCR, max pain, OI walls) per underlying and day from the F&O history, with a check against India VIX; report in `~/tradex/reports/option_metrics.md` |
| `~/tradex/deploy/tradex.sh option-screen` | One-shot screen of the option metrics (IV, skew, PCR, max pain, OI walls) against later returns and volatility, development years only; report in `~/tradex/reports/option_screen.md` |
| `~/tradex/deploy/tradex.sh sources` | Check which free outside data files (NSE F&O bhavcopy, participant OI, ban list, MWPL, FRED global series) download from the server and how far back; report in `~/tradex/reports/data_sources.md` |
| `~/tradex/deploy/tradex.sh probe` | Check how far back Groww keeps futures and options history with open interest; report in `~/tradex/reports/derivatives_probe.md` |
| `~/tradex/deploy/tradex.sh futures-history` | Load daily candles and open interest of every expired monthly future since 2020 (20–40 minutes; safe to stop and re-run) |
| `~/tradex/deploy/tradex.sh oi-study` | Open-interest study on the development years (needs `futures-history` first); report in `~/tradex/reports/oi_study.md` |
| `~/tradex/deploy/tradex.sh hourly-study` | Hourly entries inside the daily trend, development years only (load hourly history first: `backfill --timeframes 1h --days-1h 2400`); report in `~/tradex/reports/hourly_study.md` |
| `~/tradex/deploy/tradex.sh hourly-final` | One-time final test of hourly breakout (variant D) on the held-back period; report in `~/tradex/reports/hourly_final.md` |
| `~/tradex/deploy/tradex.sh vix-study` | Fetch India VIX history, then compare it with NIFTY's realised volatility on the development years; report in `~/tradex/reports/vix_study.md` |
| `~/tradex/deploy/tradex.sh screen` | One-shot screen of all 26 catalog metrics that have history against the next 5/10/20-day return and volatility surprise, development years only, corrected for testing many at once; report in `~/tradex/reports/feature_screen.md` |
| `~/tradex/deploy/tradex.sh vix-filter-final` | One-time final test of the VIX-jump volatility warning (the screen's only survivor) on the held-back period; report in `~/tradex/reports/vix_filter_final.md`. Run once only |
| `~/tradex/deploy/tradex.sh chain-fields` | Show which fields Groww sends in an option chain (stores nothing). The collector stores the chains itself every trading day from 15:15 IST |
| `~/tradex/deploy/tradex.sh logs collector` | Live collector log (Ctrl+C to leave) |
| `~/tradex/deploy/tradex.sh restart collector` | Restart one part |
| `~/tradex/deploy/tradex.sh stop` / `start` | Stop / start everything (data is kept) |
| `~/tradex/deploy/tradex.sh backup` | Back up the database now |
| `~/tradex/deploy/tradex.sh update` | Deploy the latest `main` right now instead of waiting 5 minutes |

After changing `.env`, run `start` again so the containers pick it up.

## Updates and backups

- **Updates:** every 5 minutes the server checks GitHub. If `main` has new commits it pulls
  them, applies database migrations and restarts. Merging a PR is the deploy.
  Check the last run with `journalctl -u tradex-update -n 20`.
- **Backups:** every day at 16:30 IST the database is saved to `~/tradex-backups/`
  (last 14 kept). Check with `ls -lh ~/tradex-backups`.
- **Restore** (only if something went wrong; this replaces the current data with the backup):
  ```bash
  gunzip -c ~/tradex-backups/<file>.sql.gz | docker compose --env-file ~/tradex/.env -f ~/tradex/deploy/docker-compose.yml exec -T db psql -U tradex -d tradex
  ```

## Moving to a bigger plan later

Only needed if the server feels slow or AWS has lifted the plan limit and you want headroom.
Lightsail can't resize a server in place, so you copy it:

1. `tradex-server` → **Snapshots** → **Create snapshot**.
2. When it's ready, open the snapshot's menu → **Create new instance**, pick the bigger plan, name it `tradex-server-2`.
3. **Networking** → `tradex-ip` → detach from the old server and attach to the new one.
   The address stays the same, so Groww and Tailscale need no changes.
4. Check `http://tradex` works, then delete the old server and the snapshot so they stop costing money.

If AWS still refuses the bigger plan, open a case with AWS Support asking for a Lightsail
plan-size limit increase in Mumbai.

## Rotating a key

1. Create the new key in Groww (or `/revoke` + `/token` in @BotFather for Telegram).
2. `nano ~/tradex/.env`, replace the value, save.
3. `~/tradex/deploy/tradex.sh start`.

## Troubleshooting

| Symptom | Try |
|---|---|
| `permission denied ... docker.sock` | Log out and back in (D4). |
| Dashboard won't open | Is Tailscale on on your phone? Is `status` showing `caddy` and `dashboard` up? |
| Build stops with "killed" | Swap missing: run `bash ~/tradex/deploy/setup_server.sh` again. |
| `git clone` says *Permission denied (publickey)* | The deploy key (D1) isn't added to the repo yet. |
| No Telegram messages | `test-alert`; check the token and that you messaged the bot first. |

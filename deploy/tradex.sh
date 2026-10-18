#!/usr/bin/env bash
# Everyday commands for TradeX on the server. Run from anywhere, e.g.:
#   ~/tradex/deploy/tradex.sh status
set -euo pipefail
cd "$(dirname "$0")/.."

dc() { docker compose --env-file .env -f deploy/docker-compose.yml "$@"; }
# Runs a one-off Python script inside the backend image, with the same settings as the collector.
script() { dc run --rm --no-deps collector python "scripts/$1" "${@:2}"; }

case "${1:-help}" in
  # Images are built one at a time so a 1 GB server doesn't run out of memory.
  start)      dc build migrate && dc build dashboard && dc up -d --remove-orphans ;;
  stop)       dc down ;;
  restart)    dc restart "${@:2}" ;;
  status)     dc ps ;;
  logs)       dc logs --tail 100 -f "${@:2}" ;;
  verify)     script verify_provider_capabilities.py && echo "Report: reports/provider_capability_report.md" ;;
  backfill)   script backfill_candles.py "${@:2}" ;;
  study)      script signal_study.py "${@:2}" && echo "Report: reports/signal_study.md" ;;
  variants)   script signal_study.py --variants "${@:2}" && echo "Report: reports/long_variants.md" ;;
  futures-history) script load_futures_history.py "${@:2}" ;;
  chain-fields) script option_chain_snapshot.py --fields "${@:2}" ;;
  oi-study)   script oi_study.py "${@:2}" && echo "Report: reports/oi_study.md" ;;
  hourly-study) script hourly_study.py "${@:2}" && echo "Report: reports/hourly_study.md" ;;
  hourly-final) script hourly_study.py --final "${@:2}" && echo "Report: reports/hourly_final.md" ;;
  vix-study)  script vix_study.py "${@:2}" && echo "Report: reports/vix_study.md" ;;
  probe)      script probe_derivatives_history.py "${@:2}" && echo "Report: reports/derivatives_probe.md" ;;
  final)      script signal_study.py --variants --final "${@:2}" && echo "Report: reports/final_test.md" ;;
  chat-id)    script telegram_chat_id.py ;;
  test-alert) script send_test_alert.py ;;
  backup)     deploy/backup.sh ;;
  update)     deploy/update.sh --force ;;
  *)
    cat <<'HELP'
Usage: deploy/tradex.sh <command>

  start        build and start everything (safe to run again)
  stop         stop everything (the database is kept)
  restart X    restart one service, e.g. restart collector
  status       show what is running
  logs [X]     follow the logs, e.g. logs collector   (Ctrl+C to leave)
  verify       Milestone 0: check what the Groww API really provides
  backfill     load historical candles
  study        backtest the four core readings (writes reports/signal_study.md)
  variants     compare long-only variants, development years only (reports/long_variants.md)
  futures-history  load daily candles + OI of expired futures since 2020 (safe to re-run)
  chain-fields show which fields Groww sends in an option chain (stores nothing)
  oi-study     returns after each futures OI build-up, development years only (reports/oi_study.md)
  hourly-study hourly entries inside the daily trend, development years only (reports/hourly_study.md)
  hourly-final one-time final test of hourly breakout D on held-back data (reports/hourly_final.md)
  vix-study    India VIX vs NIFTY realised volatility, development years only (reports/vix_study.md)
  probe        how far back Groww keeps futures/options history and OI (reports/derivatives_probe.md)
  final        one-time final test of variant C on held-back years (reports/final_test.md)
  chat-id      print your Telegram chat ID
  test-alert   send a test Telegram message
  backup       back up the database now
  update       pull the latest code from GitHub and restart
HELP
    ;;
esac

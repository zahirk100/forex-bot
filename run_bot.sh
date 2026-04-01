#!/usr/bin/env bash
set -euo pipefail

if [ -f .env ]; then
  # shellcheck disable=SC1091
  source .env
fi

python live_check.py --symbol "${MT5_SYMBOL:-EURUSD}"
python mt5_bot.py

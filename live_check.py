import argparse
import os
import sys

import MetaTrader5 as mt5


def connect() -> None:
    login = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")

    if login and password and server:
        ok = mt5.initialize(login=int(login), password=password, server=server)
    else:
        ok = mt5.initialize()

    if not ok:
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="MT5 pre-live readiness checker")
    parser.add_argument("--symbol", default=os.getenv("MT5_SYMBOL", "EURUSD"))
    args = parser.parse_args()

    try:
        connect()
        account = mt5.account_info()
        if account is None:
            raise RuntimeError(f"Could not read account info: {mt5.last_error()}")

        symbol = mt5.symbol_info(args.symbol)
        if symbol is None:
            raise RuntimeError(f"Symbol {args.symbol} not found in MT5")

        if not symbol.visible:
            if not mt5.symbol_select(args.symbol, True):
                raise RuntimeError(f"Could not enable symbol {args.symbol}")

        tick = mt5.symbol_info_tick(args.symbol)
        if tick is None:
            raise RuntimeError(f"No tick for {args.symbol}")

        print("✅ MT5 connection OK")
        print(f"✅ Account login: {account.login}")
        print(f"✅ Server: {account.server}")
        print(f"✅ Trade allowed: {account.trade_allowed}")
        print(f"✅ Symbol: {args.symbol}")
        print(f"✅ Bid/Ask: {tick.bid} / {tick.ask}")
        print("✅ Pre-live check passed")
        return 0
    except Exception as exc:
        print(f"❌ Pre-live check failed: {exc}")
        return 1
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())

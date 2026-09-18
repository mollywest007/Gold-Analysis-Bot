import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import alerts


class FastExitMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_monitor_checks_open_trade_from_fresh_quote(self):
        context = SimpleNamespace(application=SimpleNamespace(bot=AsyncMock()))
        trade = {
            "id": "fast-sl",
            "account_id": "123",
            "direction": "BUY",
            "entry": 100.0,
            "sl": 95.0,
            "tp1": 110.0,
            "tp2": 120.0,
            "timeframe": "M15",
            "status": "open",
        }
        check_trades = patch.object(
            alerts.trade_tracker, "check_trades", return_value=[]
        )

        with patch.object(alerts, "_load", return_value={123}), \
             patch.object(alerts, "_load_account_state", return_value=alerts.AccountAlertState()), \
             patch.object(alerts.trade_tracker, "get_active_trades", return_value=[trade]), \
             patch.object(alerts, "get_gold_price", new=AsyncMock(return_value=94.8)), \
             patch.object(alerts, "_save_signal_state"), \
             patch.object(
                 __import__("src.market_hours", fromlist=["market_status"]),
                 "market_status",
                 return_value={"is_open": True},
             ), \
             check_trades as checked:
            await alerts.check_open_trades_fast(context)

        checked.assert_called_once_with(94.8, account_id=123)


if __name__ == "__main__":
    unittest.main()
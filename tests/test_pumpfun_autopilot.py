import asyncio
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.modules.setdefault("aiohttp", types.SimpleNamespace(ClientError=Exception))
sys.modules.setdefault(
    "pydantic",
    types.SimpleNamespace(
        BaseModel=type("BaseModel", (), {}),
        Field=lambda *_, **__: None,
        ValidationError=type("ValidationError", (Exception,), {}),
        field_validator=lambda *_, **__: (lambda fn: fn),
    ),
)
sys.path.append(str(Path(__file__).resolve().parents[1]))

from sam.strategies.pumpfun_autopilot import (  # noqa: E402
    AutopilotConfig,
    PumpFunAutopilot,
    PumpFunCandidate,
    PositionState,
)


class DummyScanner:
    def __init__(self, candidates):
        self.candidates = candidates

    async def fetch_candidates(self):
        return list(self.candidates)


class DummyScorer:
    def __init__(self, value):
        self.value = value

    async def score(self, candidate):
        return self.value


class DummyPriceMonitor:
    def __init__(self, prices):
        self.prices = {mint: list(seq) for mint, seq in prices.items()}

    async def get_price(self, mint: str):
        seq = self.prices.get(mint, [])
        if not seq:
            return None
        if len(seq) > 1:
            return seq.pop(0)
        return seq[0]

    async def get_recent_trades(self, mint: str, limit: int = 3):
        price = await self.get_price(mint)
        return {"trades": [{"price": price}]} if price is not None else {"trades": []}


class DummyExecutor:
    def __init__(self, fail_first=False):
        self.buy_calls = []
        self.sell_calls = []
        self.fail_first = fail_first
        self.attempts = 0

    async def buy(self, mint: str, amount: float, *, slippage: int, priority_fee: float):
        self.attempts += 1
        if self.fail_first and self.attempts == 1:
            raise RuntimeError("network glitch")
        self.buy_calls.append((mint, amount, slippage, priority_fee))
        return {"success": True, "transaction_id": "buy"}

    async def sell(
        self, mint: str, percentage: int = 100, *, slippage: int, priority_fee: float
    ):
        self.sell_calls.append((mint, percentage, slippage, priority_fee))
        return {"success": True, "transaction_id": "sell"}


def test_autopilot_enters_and_exits_on_stop_loss():
    async def _run():
        candidate = PumpFunCandidate(mint="mint-1", symbol="AAA", price=1.0, liquidity=10000)
        scanner = DummyScanner([candidate])
        scorer = DummyScorer(0.9)
        price_monitor = DummyPriceMonitor({"mint-1": [1.0, 0.85]})
        executor = DummyExecutor()
        config = AutopilotConfig(
            max_daily_sol=1.0,
            base_position_sol=0.2,
            max_position_sol=0.2,
            stop_loss_pct=10,
            take_profit_pct=0,
            trailing_stop_pct=0,
            refresh_interval=0.01,
        )
        autopilot = PumpFunAutopilot(scanner, scorer, price_monitor, executor, config)

        await autopilot.run_once()
        assert executor.buy_calls == [("mint-1", 0.2, config.slippage, config.priority_fee)]
        assert "mint-1" in autopilot.open_positions

        await autopilot.run_once()
        assert executor.sell_calls == [("mint-1", 100, config.slippage, config.priority_fee)]
        assert "mint-1" not in autopilot.open_positions

    asyncio.run(_run())


def test_autopilot_respects_safety_limits():
    async def _run():
        candidate = PumpFunCandidate(mint="mint-2", symbol="BBB", price=1.0, liquidity=10000)
        scanner = DummyScanner([candidate])
        scorer = DummyScorer(1.0)
        price_monitor = DummyPriceMonitor({"mint-2": [1.0, 1.1]})
        executor = DummyExecutor()
        config = AutopilotConfig(
            max_daily_sol=0.2,
            base_position_sol=0.2,
            max_position_sol=0.2,
            max_open_positions=1,
            stop_loss_pct=0,
            take_profit_pct=0,
            trailing_stop_pct=0,
        )
        autopilot = PumpFunAutopilot(scanner, scorer, price_monitor, executor, config)
        autopilot.daily_spent = 0.2
        autopilot.open_positions["existing"] = PositionState(
            mint="existing",
            amount_sol=0.1,
            entry_price=1.0,
            opened_at=datetime.utcnow(),
            highest_price=1.0,
            symbol="EX",
        )

        await autopilot.run_once()
        assert executor.buy_calls == []

    asyncio.run(_run())


def test_autopilot_retries_on_failures():
    async def _run():
        candidate = PumpFunCandidate(mint="mint-3", symbol="CCC", price=1.0, liquidity=10000)
        scanner = DummyScanner([candidate])
        scorer = DummyScorer(1.0)
        price_monitor = DummyPriceMonitor({"mint-3": [1.0]})
        executor = DummyExecutor(fail_first=True)
        config = AutopilotConfig(
            max_daily_sol=1.0,
            base_position_sol=0.1,
            max_position_sol=0.1,
            stop_loss_pct=0,
            take_profit_pct=0,
            trailing_stop_pct=0,
            max_retries=2,
        )

        autopilot = PumpFunAutopilot(scanner, scorer, price_monitor, executor, config)
        await autopilot.run_once()

        assert executor.attempts == 2
        assert executor.buy_calls == [("mint-3", 0.1, config.slippage, config.priority_fee)]

    asyncio.run(_run())

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol

from sam.integrations.pump_fun import PumpFunTools

logger = logging.getLogger(__name__)


@dataclass
class PumpFunCandidate:
    """Kandydat do zakupu pochodzący ze skanera."""

    mint: str
    symbol: str
    score: float = 0.0
    liquidity: Optional[float] = None
    price: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PositionState:
    mint: str
    amount_sol: float
    entry_price: float
    opened_at: datetime
    highest_price: float
    symbol: str = ""


@dataclass
class AutopilotConfig:
    max_daily_sol: float = 1.0
    base_position_sol: float = 0.1
    max_position_sol: float = 0.5
    max_open_positions: int = 3
    stop_loss_pct: float = 10.0
    take_profit_pct: float = 30.0
    trailing_stop_pct: float = 12.0
    slippage: int = 5
    priority_fee: float = 0.00001
    refresh_interval: float = 5.0
    max_position_duration: float = 900.0
    min_score: float = 0.0
    min_liquidity: float = 0.0
    max_retries: int = 2

    def stop_loss_threshold(self) -> float:
        return max(self.stop_loss_pct, 0.0) / 100.0

    def take_profit_threshold(self) -> float:
        return max(self.take_profit_pct, 0.0) / 100.0

    def trailing_stop_threshold(self) -> float:
        return max(self.trailing_stop_pct, 0.0) / 100.0


class CandidateScanner(Protocol):
    async def fetch_candidates(self) -> Iterable[PumpFunCandidate]: ...


class CandidateScorer(Protocol):
    async def score(self, candidate: PumpFunCandidate) -> float: ...


class PriceMonitor(Protocol):
    async def get_price(self, mint: str) -> Optional[float]: ...

    async def get_recent_trades(self, mint: str, limit: int = 3) -> Mapping[str, Any]: ...


class PumpFunTradeExecutor(Protocol):
    async def buy(self, mint: str, amount: float, *, slippage: int, priority_fee: float) -> Mapping[str, Any]: ...

    async def sell(
        self, mint: str, percentage: int = 100, *, slippage: int, priority_fee: float
    ) -> Mapping[str, Any]: ...


class PumpFunToolsExecutor:
    """Adapter wykorzystujący istniejący `PumpFunTools` dla autopilota."""

    def __init__(self, pump_fun_tools: PumpFunTools) -> None:
        self.pump_fun_tools = pump_fun_tools

    async def buy(self, mint: str, amount: float, *, slippage: int, priority_fee: float) -> Mapping[str, Any]:
        public_key = getattr(self.pump_fun_tools.solana_tools, "wallet_address", None)
        if not public_key:
            return {"error": "Missing wallet for pump.fun buy"}
        return await self.pump_fun_tools.create_buy_transaction(
            public_key, mint, amount, slippage, priority_fee
        )

    async def sell(
        self, mint: str, percentage: int = 100, *, slippage: int, priority_fee: float
    ) -> Mapping[str, Any]:
        public_key = getattr(self.pump_fun_tools.solana_tools, "wallet_address", None)
        if not public_key:
            return {"error": "Missing wallet for pump.fun sell"}
        return await self.pump_fun_tools.create_sell_transaction(
            public_key, mint, percentage, slippage, priority_fee
        )


class PumpFunPriceMonitor:
    """Monitor wykorzystujący pump.fun API do odczytu ostatnich transakcji."""

    def __init__(self, pump_fun_tools: PumpFunTools) -> None:
        self.pump_fun_tools = pump_fun_tools

    async def get_price(self, mint: str) -> Optional[float]:
        trades = await self.get_recent_trades(mint)
        trade_list = trades.get("trades") if isinstance(trades, Mapping) else None
        if not trade_list:
            return None
        latest = trade_list[0]
        try:
            return float(latest.get("price") or latest.get("priceUsd") or latest.get("value"))
        except Exception:
            return None

    async def get_recent_trades(self, mint: str, limit: int = 3) -> Mapping[str, Any]:
        return await self.pump_fun_tools.get_token_trades(mint, limit)


class PumpFunAutopilot:
    """Asynchroniczny autopilot dla strategii pump.fun."""

    def __init__(
        self,
        scanner: CandidateScanner,
        scorer: CandidateScorer,
        price_monitor: PriceMonitor,
        executor: PumpFunTradeExecutor,
        config: AutopilotConfig,
    ) -> None:
        self.scanner = scanner
        self.scorer = scorer
        self.price_monitor = price_monitor
        self.executor = executor
        self.config = config
        self.open_positions: Dict[str, PositionState] = {}
        self.daily_spent: float = 0.0
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        logger.info("Uruchamiam autopilota pump.fun z interwałem %.2fs", self.config.refresh_interval)
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception as exc:  # pragma: no cover - defensywne logowanie
                logger.exception("Błąd pętli autopilota: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.config.refresh_interval)
            except asyncio.TimeoutError:
                continue

    async def run_once(self) -> None:
        await self._evaluate_entries()
        await self._evaluate_positions()

    async def _evaluate_entries(self) -> None:
        candidates = await self.scanner.fetch_candidates()
        for candidate in candidates:
            score = await self.scorer.score(candidate)
            candidate.score = score

            if score < self.config.min_score:
                logger.debug("Pomijam %s: score %.2f < próg %.2f", candidate.mint, score, self.config.min_score)
                continue
            if candidate.liquidity is not None and candidate.liquidity < self.config.min_liquidity:
                logger.info(
                    "Pomijam %s z powodu niskiej płynności %.2f < %.2f",
                    candidate.mint,
                    candidate.liquidity,
                    self.config.min_liquidity,
                )
                continue
            if candidate.mint in self.open_positions:
                continue
            if len(self.open_positions) >= self.config.max_open_positions:
                logger.warning("Osiągnięto limit otwartych pozycji: %s", self.config.max_open_positions)
                return
            if self.daily_spent >= self.config.max_daily_sol:
                logger.warning("Wyczerpano dzienny limit SOL: %.2f", self.config.max_daily_sol)
                return

            size = self._position_size(score)
            remaining = self.config.max_daily_sol - self.daily_spent
            size = min(size, remaining, self.config.max_position_sol)
            if size <= 0:
                logger.debug("Pominięto %s: wyliczony rozmiar <= 0", candidate.mint)
                continue

            logger.info(
                "Kupuję %s (%s) za %.4f SOL (score=%.2f, slippage=%s, priority=%.5f)",
                candidate.symbol,
                candidate.mint,
                size,
                score,
                self.config.slippage,
                self.config.priority_fee,
            )
            result = await self._execute_with_retry(
                self.executor.buy,
                candidate.mint,
                size,
                slippage=self.config.slippage,
                priority_fee=self.config.priority_fee,
            )
            if result and "success" in result:
                entry_price = candidate.price or await self.price_monitor.get_price(candidate.mint) or 0.0
                opened = PositionState(
                    mint=candidate.mint,
                    amount_sol=size,
                    entry_price=entry_price,
                    opened_at=datetime.utcnow(),
                    highest_price=entry_price,
                    symbol=candidate.symbol,
                )
                self.open_positions[candidate.mint] = opened
                self.daily_spent += size
                logger.info("Pozycja otwarta dla %s; łączny dzienny koszt %.4f SOL", candidate.mint, self.daily_spent)
            elif result and result.get("error"):
                logger.error("Nieudany zakup %s: %s", candidate.mint, result.get("error"))

    async def _evaluate_positions(self) -> None:
        to_close: List[str] = []
        for mint, position in list(self.open_positions.items()):
            price = await self.price_monitor.get_price(mint)
            if price is None:
                logger.debug("Brak ceny dla %s - pomijam", mint)
                continue

            # Aktualizacja trailing stop
            if price > position.highest_price:
                position.highest_price = price

            if self._should_stop_loss(price, position):
                logger.info("Stop-loss aktywny dla %s przy cenie %.4f", mint, price)
                to_close.append(mint)
            elif self._should_take_profit(price, position):
                logger.info("Take-profit aktywny dla %s przy cenie %.4f", mint, price)
                to_close.append(mint)
            elif self._should_trailing_stop(price, position):
                logger.info("Trailing-stop aktywny dla %s przy cenie %.4f", mint, price)
                to_close.append(mint)
            elif self._exceeds_max_duration(position):
                logger.info("Limit czasu pozycji przekroczony dla %s", mint)
                to_close.append(mint)

        for mint in to_close:
            await self._close_position(mint)

    async def _close_position(self, mint: str) -> None:
        position = self.open_positions.get(mint)
        if not position:
            return

        result = await self._execute_with_retry(
            self.executor.sell,
            mint,
            100,
            slippage=self.config.slippage,
            priority_fee=self.config.priority_fee,
        )
        if result and "success" in result:
            logger.info("Zamknięto pozycję %s", mint)
            self.open_positions.pop(mint, None)
        elif result and result.get("error"):
            logger.error("Błąd zamykania pozycji %s: %s", mint, result.get("error"))

    async def _execute_with_retry(self, func, *args, **kwargs) -> Optional[Mapping[str, Any]]:
        attempt = 0
        last_error: Optional[Exception] = None
        while attempt <= self.config.max_retries:
            try:
                return await func(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - defensywne
                last_error = exc
                attempt += 1
                logger.warning("Błąd wykonania (próba %s/%s): %s", attempt, self.config.max_retries, exc)
                await asyncio.sleep(min(1.0, 0.25 * attempt))
        if last_error:
            logger.error("Rezygnuję po nieudanych próbach: %s", last_error)
        return None

    def _position_size(self, score: float) -> float:
        boost = max(score - self.config.min_score, 0.0)
        size = self.config.base_position_sol * (1 + boost)
        return min(size, self.config.max_position_sol)

    def _should_stop_loss(self, price: float, position: PositionState) -> bool:
        if self.config.stop_loss_pct <= 0 or position.entry_price <= 0:
            return False
        return price <= position.entry_price * (1 - self.config.stop_loss_threshold())

    def _should_take_profit(self, price: float, position: PositionState) -> bool:
        if self.config.take_profit_pct <= 0 or position.entry_price <= 0:
            return False
        return price >= position.entry_price * (1 + self.config.take_profit_threshold())

    def _should_trailing_stop(self, price: float, position: PositionState) -> bool:
        if self.config.trailing_stop_pct <= 0 or position.highest_price <= 0:
            return False
        threshold = position.highest_price * (1 - self.config.trailing_stop_threshold())
        return price <= threshold and price > 0

    def _exceeds_max_duration(self, position: PositionState) -> bool:
        if self.config.max_position_duration <= 0:
            return False
        return datetime.utcnow() - position.opened_at > timedelta(seconds=self.config.max_position_duration)

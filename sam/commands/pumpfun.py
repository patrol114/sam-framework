"""Polecenia CLI dla pump.fun korzystające z prawdziwego API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..config.settings import Settings
from ..integrations.pump_fun import PumpFunTools
from ..integrations.solana.solana_tools import SolanaTools
from ..utils.cli_helpers import CLIFormatter
from ..utils.crypto import decrypt_private_key
from ..utils.secure_storage import get_secure_storage
from ..utils.validators import SellPercentage, SlippageTolerance, SolanaAddress, TradeAmount

logger = logging.getLogger(__name__)


@dataclass
class PumpFunTradeResult:
    """Wynik wykonania realnego zlecenia pump.fun."""

    exit_code: int
    message: str
    details: Dict[str, Any]


def _load_private_key() -> Optional[str]:
    """Pobierz klucz prywatny z bezpiecznego magazynu lub zmiennej środowiskowej."""

    storage = get_secure_storage()
    private_key = storage.get_private_key("default")

    if not private_key and Settings.SAM_WALLET_PRIVATE_KEY:
        try:
            candidate = Settings.SAM_WALLET_PRIVATE_KEY
            if candidate.startswith("gAAAAA"):
                candidate = decrypt_private_key(candidate)
            storage.store_private_key("default", candidate)
            private_key = candidate
        except Exception as exc:  # pragma: no cover - defensywny logging
            logger.error("Nie udało się odszyfrować lub zapisać klucza: %s", exc)

    return private_key


async def run_pumpfun_trade(
    *,
    mint: str,
    amount_sol: Optional[float] = None,
    percentage: Optional[int] = None,
    slippage: int = 5,
    action: str = "buy",
) -> PumpFunTradeResult:
    """Wykonaj realne zlecenie pump.fun z podpisem portfela i prawdziwym API.

    Parametry:
        mint: adres mint tokenu.
        amount_sol: kwota SOL dla zakupu (wymagana dla akcji "buy").
        percentage: procent posiadanego tokenu do sprzedaży (wymagane dla "sell").
        slippage: poślizg cenowy w punktach bazowych.
        action: "buy" lub "sell".
    """

    if action not in {"buy", "sell"}:
        return PumpFunTradeResult(
            exit_code=1,
            message="❌ Nieobsługiwana akcja pump.fun. Użyj buy lub sell.",
            details={"action": action},
        )

    try:
        validated_mint = SolanaAddress(address=mint).address
    except ValueError as exc:
        return PumpFunTradeResult(
            exit_code=1,
            message=f"❌ Nieprawidłowy adres mint: {exc}",
            details={"mint": mint, "action": action},
        )

    try:
        validated_slippage = SlippageTolerance(slippage=slippage).slippage
    except ValueError as exc:
        return PumpFunTradeResult(
            exit_code=1,
            message=f"❌ Nieprawidłowy poślizg cenowy: {exc}",
            details={"mint": validated_mint, "action": action},
        )

    if action == "buy":
        if amount_sol is None:
            return PumpFunTradeResult(
                exit_code=1,
                message="❌ Dla zakupu ustaw dodatnią kwotę SOL (--amount).",
                details={"mint": validated_mint, "action": action},
            )
        try:
            validated_amount = TradeAmount(amount=amount_sol).amount
        except ValueError as exc:
            return PumpFunTradeResult(
                exit_code=1,
                message=f"❌ Nieprawidłowa kwota transakcji: {exc}",
                details={"mint": validated_mint, "action": action},
            )
    else:
        if percentage is None:
            return PumpFunTradeResult(
                exit_code=1,
                message="❌ Dla sprzedaży ustaw procent pozycji (--percentage).",
                details={"mint": validated_mint, "action": action},
            )
        try:
            validated_amount = SellPercentage(percentage=percentage).percentage
        except ValueError as exc:
            return PumpFunTradeResult(
                exit_code=1,
                message=f"❌ Nieprawidłowy procent sprzedaży: {exc}",
                details={"mint": validated_mint, "action": action},
            )

    private_key = _load_private_key()
    if not private_key:
        return PumpFunTradeResult(
            exit_code=1,
            message=(
                "❌ Brak skonfigurowanego klucza portfela. Ustaw SAM_WALLET_PRIVATE_KEY lub "
                "dodaj klucz w secure storage."
            ),
            details={"mint": validated_mint, "action": action},
        )

    solana_tools = SolanaTools(Settings.SAM_SOLANA_RPC_URL, private_key)
    if not solana_tools.wallet_address:
        return PumpFunTradeResult(
            exit_code=1,
            message="❌ Nie udało się odczytać adresu portfela z klucza prywatnego.",
            details={"mint": validated_mint, "action": action},
        )

    pump_fun_tools = PumpFunTools(solana_tools)

    if action == "buy":
        result = await pump_fun_tools.create_buy_transaction(
            solana_tools.wallet_address, validated_mint, validated_amount, validated_slippage
        )
    else:
        result = await pump_fun_tools.create_sell_transaction(
            solana_tools.wallet_address, validated_mint, validated_amount, validated_slippage
        )

    if result.get("error"):
        message = CLIFormatter.error(f"pump.fun {action} niepowodzenie: {result['error']}")
        return PumpFunTradeResult(exit_code=1, message=message, details=result)

    tx_id = result.get("transaction_id") or result.get("signature")
    summary = (
        f"✅ pump.fun {action} zakończone. Mint={validated_mint}, slippage={validated_slippage}%, "
        f"tx={tx_id}"
    )
    if action == "buy":
        summary += f", amount={validated_amount} SOL"
    else:
        summary += f", percentage={validated_amount}%"

    print(CLIFormatter.success(summary))
    return PumpFunTradeResult(exit_code=0, message=summary, details=result)

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Protocol


class HttpClientProtocol(Protocol):
    async def get_json(self, url: str, headers: Optional[Dict[str, str]] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]: ...


class PriceProviderError(RuntimeError):
    pass


class PriceClient:
    """
    Bezpieczny klient cen:
    - próbuje skonfigurowany provider (może wymagać API key),
    - jeśli dostanie 401/Unauthorized -> fallback do publicznego Jupitera.
    """

    def __init__(self, http: HttpClientProtocol):
        self.http = http

        # Uwaga: nazwy env dopasuj do swojego repo – tu celowo proste i neutralne.
        self.primary_url = os.getenv("SAM_PRICE_API_URL", "").strip()
        self.primary_key = os.getenv("SAM_PRICE_API_KEY", "").strip()

        # Jupiter price – publiczny (Solana).
        self.jupiter_url = os.getenv("SAM_JUPITER_PRICE_URL", "https://price.jup.ag/v6/price").strip()

    async def get_token_price_usd(self, symbol_or_mint: str) -> float:
        # 1) Primary provider (jeśli skonfigurowany)
        if self.primary_url:
            try:
                headers = {}
                if self.primary_key:
                    headers["Authorization"] = f"Bearer {self.primary_key}"

                data = await self.http.get_json(
                    self.primary_url,
                    headers=headers or None,
                    params={"q": symbol_or_mint},
                )

                # Dostosuj do swojego formatu – minimalny, defensywny parser:
                price = (
                    data.get("price")
                    or data.get("data", {}).get("price")
                    or data.get("result", {}).get("price")
                )
                if price is None:
                    raise PriceProviderError(f"Primary provider zwrócił brak ceny dla: {symbol_or_mint}")

                return float(price)

            except Exception as e:
                # Jeśli to 401 – fallback do Jupitera.
                msg = str(e).lower()
                if "401" not in msg and "unauthorized" not in msg:
                    # Inne błędy też mogą fallbackować, ale zostawiamy je czytelne:
                    raise

        # 2) Fallback: Jupiter
        # Jupiter działa dla ID/mint (np. SOL ma alias), ale w praktyce najlepiej podawać mint.
        # Jeśli w Twoim flow używasz symboli – dołóż mapowanie symbol->mint w jednym miejscu.
        j = await self.http.get_json(self.jupiter_url, params={"ids": symbol_or_mint})
        node = (j.get("data") or {}).get(symbol_or_mint) or {}

        price = node.get("price")
        if price is None:
            raise PriceProviderError(f"Jupiter nie zwrócił ceny dla: {symbol_or_mint}")

        return float(price)

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol


class TokenInfo(Protocol):
    address: str
    name: Optional[str]
    symbol: Optional[str]


class PriceWindow(Protocol):
    h1: Optional[float]
    h6: Optional[float]
    h24: Optional[float]


class VolumeWindow(Protocol):
    h1: Optional[float]
    h6: Optional[float]
    h24: Optional[float]


class LiquidityInfo(Protocol):
    usd: Optional[float]


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and value.strip() == "":
            return None
        return float(value)
    except Exception:
        return None


@dataclass(slots=True)
class TokenPair:
    """
    Uniwersalny model pary/tokena do narzędzi market-data.

    Cel:
    - nie wywalać AttributeError na brakujących polach,
    - trzymać kluczowe metryki (market_cap, liquidity, volume itd.),
    - pozwolić na przechowanie "raw" danych z providerów.
    """

    # Identyfikatory / metadane
    chain_id: Optional[str] = None
    dex_id: Optional[str] = None
    url: Optional[str] = None
    pair_address: Optional[str] = None

    # Token objects (for compatibility with dexscreener library)
    base_token: Optional[TokenInfo] = None
    quote_token: Optional[TokenInfo] = None

    # Legacy flat attributes (for backward compatibility)
    base_symbol: Optional[str] = None
    base_address: Optional[str] = None
    quote_symbol: Optional[str] = None
    quote_address: Optional[str] = None

    # Ceny / metryki
    price_usd: Optional[float] = None
    market_cap: Optional[float] = None
    fdv: Optional[float] = None

    # Window objects (for compatibility with dexscreener library)
    price_change: Optional[PriceWindow] = None
    volume: Optional[VolumeWindow] = None
    liquidity: Optional[LiquidityInfo] = None

    # Legacy flat attributes (for backward compatibility)
    liquidity_usd: Optional[float] = None
    volume_24h: Optional[float] = None
    price_change_24h: Optional[float] = None

    # Additional metadata
    pair_created_at: Optional[str] = None
    info: Optional[Dict[str, Any]] = None

    # Surowe dane providera (do debug/telemetrii)
    raw: Dict[str, Any] = field(default_factory=dict)

    def safe_get(self, key: str, default: Any = None) -> Any:
        """
        Bezpieczny getter: najpierw atrybut, potem raw.
        """
        if not key:
            return default
        if hasattr(self, key):
            return getattr(self, key)
        return self.raw.get(key, default)

    @classmethod
    def from_dexscreener(cls, pair: Any) -> "TokenPair":
        """
        Mapowanie typowej odpowiedzi DexScreener -> TokenPair.
        (Działa też, gdy część pól nie występuje.)
        Obsługuje zarówno dict jak i TokenPair obiekty z biblioteki dexscreener.
        """
        if hasattr(pair, 'chain_id'):  # TokenPair object from dexscreener library
            return cls(
                chain_id=pair.chain_id,
                dex_id=pair.dex_id,
                url=pair.url,
                pair_address=pair.pair_address,
                base_token=pair.base_token,
                quote_token=pair.quote_token,
                price_usd=pair.price_usd,
                fdv=pair.fdv,
                price_change=pair.price_change,
                volume=pair.volume,
                liquidity=pair.liquidity,
                pair_created_at=pair.pair_created_at.isoformat() if pair.pair_created_at else None,
                raw=pair.dict() if hasattr(pair, 'dict') else {},
            )
        else:  # dict response
            base = pair.get("baseToken") or {}
            quote = pair.get("quoteToken") or {}
            liquidity = pair.get("liquidity") or {}
            volume = pair.get("volume") or {}
            price_change = pair.get("priceChange") or {}

            return cls(
                chain_id=pair.get("chainId"),
                dex_id=pair.get("dexId"),
                url=pair.get("url"),
                pair_address=pair.get("pairAddress") or pair.get("pair_address"),

                base_symbol=base.get("symbol"),
                base_address=base.get("address"),
                quote_symbol=quote.get("symbol"),
                quote_address=quote.get("address"),

                price_usd=_to_float(pair.get("priceUsd")),
                market_cap=_to_float(pair.get("marketCap")),   # bywa brak lub inna nazwa – zostaje None
                fdv=_to_float(pair.get("fdv")),
                liquidity_usd=_to_float(liquidity.get("usd")),
                volume_24h=_to_float(volume.get("h24")),
                price_change_24h=_to_float(price_change.get("h24")),

                raw=pair,
            )

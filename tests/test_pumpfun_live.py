import json

import pytest
from solders.keypair import Keypair

from sam.commands import pumpfun


@pytest.mark.asyncio
async def test_pumpfun_trade_buy(monkeypatch):
    keypair = Keypair()
    monkeypatch.setattr(
        pumpfun.Settings, "SAM_WALLET_PRIVATE_KEY", json.dumps(list(keypair.to_bytes_array()))
    )

    captured = {}

    async def fake_buy(self, public_key, mint, amount, slippage):
        captured.update(
            {
                "public_key": public_key,
                "mint": mint,
                "amount": amount,
                "slippage": slippage,
            }
        )
        return {"success": True, "transaction_id": "demo-tx"}

    monkeypatch.setattr(pumpfun.PumpFunTools, "create_buy_transaction", fake_buy)

    result = await pumpfun.run_pumpfun_trade(
        mint=str(Keypair().pubkey()), amount_sol=0.015, slippage=7, action="buy"
    )

    assert result.exit_code == 0
    assert captured["mint"]
    assert captured["amount"] == 0.015
    assert captured["slippage"] == 7
    assert captured["public_key"] == str(keypair.pubkey())


@pytest.mark.asyncio
async def test_pumpfun_trade_sell(monkeypatch):
    keypair = Keypair()
    monkeypatch.setattr(
        pumpfun.Settings, "SAM_WALLET_PRIVATE_KEY", json.dumps(list(keypair.to_bytes_array()))
    )

    captured = {}

    async def fake_sell(self, public_key, mint, percentage, slippage):
        captured.update(
            {
                "public_key": public_key,
                "mint": mint,
                "percentage": percentage,
                "slippage": slippage,
            }
        )
        return {"success": True, "transaction_id": "demo-tx-sell"}

    monkeypatch.setattr(pumpfun.PumpFunTools, "create_sell_transaction", fake_sell)

    result = await pumpfun.run_pumpfun_trade(
        mint=str(Keypair().pubkey()), percentage=25, slippage=4, action="sell"
    )

    assert result.exit_code == 0
    assert captured["percentage"] == 25
    assert captured["slippage"] == 4
    assert captured["public_key"] == str(keypair.pubkey())


@pytest.mark.asyncio
async def test_pumpfun_trade_validates_inputs(monkeypatch):
    keypair = Keypair()
    monkeypatch.setattr(
        pumpfun.Settings, "SAM_WALLET_PRIVATE_KEY", json.dumps(list(keypair.to_bytes_array()))
    )

    result_invalid_mint = await pumpfun.run_pumpfun_trade(
        mint="short", amount_sol=0.01, action="buy"
    )
    assert result_invalid_mint.exit_code == 1
    assert "mint" in result_invalid_mint.message

    result_invalid_slippage = await pumpfun.run_pumpfun_trade(
        mint=str(Keypair().pubkey()), amount_sol=0.01, slippage=99, action="buy"
    )
    assert result_invalid_slippage.exit_code == 1
    assert "poślizg" in result_invalid_slippage.message

    result_invalid_percentage = await pumpfun.run_pumpfun_trade(
        mint=str(Keypair().pubkey()), percentage=0, action="sell"
    )
    assert result_invalid_percentage.exit_code == 1

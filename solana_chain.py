"""
Solana chain analyzer with real price derivation.

PRICE DERIVATION STRATEGY:
Solscan's transfer endpoints show SPL token movements. For a DEX swap,
the same transaction signature will contain:
  - Token transfer (our target token in/out)
  - SOL or stablecoin transfer (the other side of the swap)

We fetch both the target token's transfers AND all SPL transfers for the
wallet, group by tx signature, and derive the swap price.

For SOL<->token swaps we convert SOL to USD via DexScreener.
For USDC/USDT<->token swaps we get direct USD value.
"""

import time
from typing import Optional
from chains.base_analyzer import ChainAnalyzer
from utils import rate_limited_get, get_dexscreener_price


# Known Solana stablecoin mints
SOL_STABLECOINS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj",  # stSOL (liquid staking)
}

# Wrapped SOL mint
WSOL_MINT = "So11111111111111111111111111111111111111112"


class SolanaChainAnalyzer(ChainAnalyzer):
    """Analyzer for Solana using Solscan public API + DexScreener."""

    SOLSCAN_API = "https://public-api.solscan.io"
    api_delay = 0.35

    def __init__(self):
        self._sol_usd_price: Optional[float] = None

    def get_token_info(self, contract_address: str) -> Optional[dict]:
        """Fetch token info from DexScreener + Solscan."""
        info = get_dexscreener_price(contract_address)
        if not info:
            return None

        meta = rate_limited_get(
            f"{self.SOLSCAN_API}/token/meta",
            params={"token": contract_address},
            delay=self.api_delay,
        )
        if meta:
            info["decimals"] = int(meta.get("decimals", 9))
            if not info.get("name"):
                info["name"] = meta.get("name", "Unknown")
            if not info.get("symbol"):
                info["symbol"] = meta.get("symbol", "???")
        else:
            info["decimals"] = 9

        # Cache SOL price
        self._sol_usd_price = self._fetch_sol_usd_price()
        if self._sol_usd_price:
            print(f"   💲 SOL price: ${self._sol_usd_price:,.2f}")

        return info

    def _fetch_sol_usd_price(self) -> Optional[float]:
        """Get SOL/USD price from DexScreener."""
        try:
            import requests
            resp = requests.get(
                "https://api.dexscreener.com/latest/dex/search",
                params={"q": "SOL USDC"},
                timeout=10,
            )
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                for p in pairs:
                    base = p.get("baseToken", {}).get("symbol", "").upper()
                    quote = p.get("quoteToken", {}).get("symbol", "").upper()
                    chain = p.get("chainId", "")
                    if chain == "solana" and "SOL" in base and ("USDC" in quote or "USDT" in quote):
                        return float(p.get("priceUsd", 0) or 0)
        except Exception:
            pass
        return 180.0  # Fallback

    def get_top_holders(self, contract_address: str, limit: int = 500) -> list[dict]:
        """Fetch top token holders from Solscan."""
        holders = []
        offset = 0
        page_size = 50

        print("   📊 Fetching holders from Solscan...")

        while len(holders) < limit:
            data = rate_limited_get(
                f"{self.SOLSCAN_API}/token/holders",
                params={
                    "tokenAddress": contract_address,
                    "offset": offset,
                    "limit": page_size,
                },
                delay=self.api_delay,
            )
            if not data or not isinstance(data, dict):
                break

            items = data.get("data", [])
            if not items:
                break

            total_supply = float(data.get("total", 1))

            for item in items:
                holders.append({
                    "address": item.get("address", ""),
                    "balance": float(item.get("amount", 0)),
                    "pct_supply": float(item.get("amount", 0)) / total_supply * 100 if total_supply > 0 else 0,
                })

            if len(items) < page_size:
                break
            offset += page_size

            if len(holders) % 100 == 0:
                print(f"   📊 Fetched {len(holders)} holders...")

        return holders[:limit]

    def analyze_wallet(
        self,
        contract_address: str,
        wallet_address: str,
        current_price: float,
        token_decimals: int,
    ) -> Optional[dict]:
        """
        Analyze wallet by fetching all SPL transfers, grouping by tx signature,
        and deriving swap prices from paired SOL/stablecoin movements.
        
        API calls per wallet: 2
          1. splTransfers for this wallet (all tokens)
          2. SOL transfers for this wallet (native SOL swaps)
        """
        decimals_factor = 10 ** token_decimals
        contract_lower = contract_address.lower()

        # 1. Fetch ALL SPL token transfers for this wallet
        all_spl_transfers = self._fetch_spl_transfers(wallet_address)

        # Separate our target token transfers vs other tokens
        target_transfers = []
        other_transfers = []

        for tx in all_spl_transfers:
            token_addr = tx.get("tokenAddress", "")
            if token_addr == contract_address:
                target_transfers.append(tx)
            else:
                other_transfers.append(tx)

        if not target_transfers:
            return self._empty_analysis()

        # 2. Fetch SOL (native) transfers
        sol_transfers = self._fetch_sol_transfers(wallet_address)

        # Build signature -> SOL amount map
        sol_by_sig = {}
        sol_price = self._sol_usd_price or 180
        wallet_lower = wallet_address.lower()

        for tx in sol_transfers:
            sig = tx.get("txHash", tx.get("signature", ""))
            src = tx.get("src", "").lower()
            dst = tx.get("dst", "").lower()
            lamports = float(tx.get("lamport", tx.get("amount", 0)))
            sol_amount = lamports / 1e9

            if sol_amount <= 0:
                continue

            if dst == wallet_lower:
                sol_by_sig[sig] = sol_by_sig.get(sig, 0) + sol_amount
            elif src == wallet_lower:
                sol_by_sig[sig] = sol_by_sig.get(sig, 0) - sol_amount

        # Build signature -> stablecoin/WSOL USD amount map
        paired_usd_by_sig = {}
        for tx in other_transfers:
            token_addr = tx.get("tokenAddress", "")
            sig = tx.get("_id", tx.get("signature", tx.get("txHash", "")))

            src = (tx.get("src", tx.get("source", "")) or "").lower()
            dst = (tx.get("dst", tx.get("destination", "")) or "").lower()
            raw_amount = float(tx.get("amount", tx.get("tokenAmount", 0)))
            dec = int(tx.get("decimals", tx.get("tokenDecimal", 9)))
            amount = raw_amount / (10 ** dec) if raw_amount > (10 ** dec) else raw_amount

            if amount <= 0:
                continue

            if token_addr in SOL_STABLECOINS:
                if dst == wallet_lower:
                    paired_usd_by_sig[sig] = paired_usd_by_sig.get(sig, 0) + amount
                elif src == wallet_lower:
                    paired_usd_by_sig[sig] = paired_usd_by_sig.get(sig, 0) - amount

            elif token_addr == WSOL_MINT:
                if dst == wallet_lower:
                    paired_usd_by_sig[sig] = paired_usd_by_sig.get(sig, 0) + (amount * sol_price)
                elif src == wallet_lower:
                    paired_usd_by_sig[sig] = paired_usd_by_sig.get(sig, 0) - (amount * sol_price)

        # Process target token transfers
        buys = []
        sells = []
        first_buy_time = None
        last_activity_time = None

        for tx in target_transfers:
            sig = tx.get("_id", tx.get("signature", tx.get("txHash", "")))
            src = (tx.get("src", tx.get("source", tx.get("owner", ""))) or "").lower()
            dst = (tx.get("dst", tx.get("destination", "")) or "").lower()
            raw_amount = float(tx.get("amount", tx.get("tokenAmount", 0)))
            token_amount = raw_amount / decimals_factor if raw_amount > decimals_factor else raw_amount
            timestamp = int(tx.get("blockTime", tx.get("block_time", 0)))

            if token_amount <= 0:
                continue

            if last_activity_time is None or timestamp > last_activity_time:
                last_activity_time = timestamp

            # Find USD value from paired transfers
            usd_value = None
            if sig in paired_usd_by_sig:
                usd_value = abs(paired_usd_by_sig[sig])
            if usd_value is None and sig in sol_by_sig:
                usd_value = abs(sol_by_sig[sig]) * sol_price

            if dst == wallet_lower:
                buys.append((token_amount, usd_value or 0, timestamp))
                if first_buy_time is None:
                    first_buy_time = timestamp
            elif src == wallet_lower:
                sells.append((token_amount, usd_value or 0, timestamp))

        # Compute metrics
        total_bought = sum(b[0] for b in buys)
        total_sold = sum(s[0] for s in sells)
        total_cost = sum(b[1] for b in buys)
        total_revenue = sum(s[1] for s in sells)

        avg_entry_price = (total_cost / total_bought) if total_bought > 0 and total_cost > 0 else None
        avg_exit_price = (total_revenue / total_sold) if total_sold > 0 and total_revenue > 0 else None

        current_holding = total_bought - total_sold
        holding_value_usd = current_holding * current_price

        if total_bought > 0 and total_cost > 0 and total_sold > 0:
            cost_per_token = total_cost / total_bought
            realized_pnl = total_revenue - (total_sold * cost_per_token)
        else:
            realized_pnl = 0

        if total_bought > 0 and total_cost > 0 and current_holding > 0:
            cost_per_token = total_cost / total_bought
            unrealized_pnl = holding_value_usd - (current_holding * cost_per_token)
        else:
            unrealized_pnl = 0

        total_pnl = realized_pnl + unrealized_pnl
        pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

        return {
            "avg_entry_price": avg_entry_price,
            "avg_exit_price": avg_exit_price,
            "total_bought": total_bought,
            "total_sold": total_sold,
            "total_cost_usd": total_cost,
            "total_revenue_usd": total_revenue,
            "current_holding": current_holding,
            "holding_value_usd": holding_value_usd,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": total_pnl,
            "pnl_pct": pnl_pct,
            "first_buy_time": first_buy_time,
            "last_activity_time": last_activity_time,
            "num_buys": len(buys),
            "num_sells": len(sells),
            "num_buys_with_price": sum(1 for b in buys if b[1] > 0),
            "num_sells_with_price": sum(1 for s in sells if s[1] > 0),
            "is_in_profit": total_pnl > 0,
            "is_diamond_hands": len(sells) == 0 and current_holding > 0,
        }

    def _fetch_spl_transfers(self, wallet_address: str, max_pages: int = 10) -> list:
        """Fetch all SPL token transfers for a wallet."""
        all_transfers = []
        offset = 0

        for _ in range(max_pages):
            data = rate_limited_get(
                f"{self.SOLSCAN_API}/account/splTransfers",
                params={
                    "account": wallet_address,
                    "offset": offset,
                    "limit": 50,
                },
                delay=self.api_delay,
            )
            if data and isinstance(data, dict):
                items = data.get("data", [])
                if not items:
                    break
                all_transfers.extend(items)
                if len(items) < 50:
                    break
                offset += 50
            else:
                break

        return all_transfers

    def _fetch_sol_transfers(self, wallet_address: str, max_pages: int = 5) -> list:
        """Fetch native SOL transfers for a wallet."""
        all_transfers = []
        offset = 0

        for _ in range(max_pages):
            data = rate_limited_get(
                f"{self.SOLSCAN_API}/account/solTransfers",
                params={
                    "account": wallet_address,
                    "offset": offset,
                    "limit": 50,
                },
                delay=self.api_delay,
            )
            if data and isinstance(data, dict):
                items = data.get("data", [])
                if not items:
                    break
                all_transfers.extend(items)
                if len(items) < 50:
                    break
                offset += 50
            else:
                break

        return all_transfers

    def _empty_analysis(self) -> dict:
        return {
            "avg_entry_price": None,
            "avg_exit_price": None,
            "total_bought": 0,
            "total_sold": 0,
            "total_cost_usd": 0,
            "total_revenue_usd": 0,
            "current_holding": 0,
            "holding_value_usd": 0,
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "total_pnl": 0,
            "pnl_pct": 0,
            "first_buy_time": None,
            "last_activity_time": None,
            "num_buys": 0,
            "num_sells": 0,
            "num_buys_with_price": 0,
            "num_sells_with_price": 0,
            "is_in_profit": False,
            "is_diamond_hands": False,
        }

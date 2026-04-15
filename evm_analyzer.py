"""
Shared EVM analyzer logic for Etherscan-compatible APIs (Base & BSC).

PRICE DERIVATION STRATEGY:
For each wallet's token txs, we also fetch their normal (ETH/BNB) txs
and internal txs for the same period. A DEX swap produces:
  - A token transfer (ERC20 tokentx) 
  - An ETH/BNB transfer (normal tx or internal tx) in the SAME tx hash
By matching these, we get: ETH_spent / tokens_received = entry_price_in_ETH.
Then we convert ETH->USD using the native token's USD price from DexScreener.
"""

import time
from typing import Optional
from chains.base_analyzer import ChainAnalyzer
from utils import rate_limited_get, get_dexscreener_price


# Wrapped native token addresses (WETH on Base, WBNB on BSC)
WETH_ADDRESSES = {
    "0x4200000000000000000000000000000000000006",  # WETH on Base
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB on BSC
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH on Ethereum
}

# Common stablecoin addresses (multi-chain)
STABLECOIN_ADDRESSES = {
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC on Base
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # USDC on BSC
    "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2",  # USDT on Base
    "0x55d398326f99059ff775485246999027b3197955",  # USDT on BSC
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI on Base
    "0x1af3f329e8be154074d8769d1ffa4ee058b1dbc3",  # DAI on BSC
}


class EVMAnalyzer(ChainAnalyzer):
    """
    Analyzer for EVM chains using Etherscan-compatible explorer APIs.
    Subclasses set explorer_base_url, chain_id, native_price_symbol.
    """

    explorer_base_url: str = ""
    chain_id: str = ""
    native_price_symbol: str = "ETH"  # Override to "BNB" for BSC
    api_delay: float = 0.25

    def __init__(self):
        self._native_usd_price: Optional[float] = None

    def get_token_info(self, contract_address: str) -> Optional[dict]:
        """Fetch token info from DexScreener + explorer."""
        info = get_dexscreener_price(contract_address)
        if not info:
            return None

        info["decimals"] = self._get_token_decimals(contract_address)

        # Cache native token price for swap calculations
        self._native_usd_price = self._fetch_native_usd_price()
        if self._native_usd_price:
            print(f"   💲 {self.native_price_symbol} price: ${self._native_usd_price:,.2f}")

        return info

    def _get_token_decimals(self, contract_address: str) -> int:
        return 18

    def _fetch_native_usd_price(self) -> Optional[float]:
        """Get current ETH or BNB price in USD from DexScreener."""
        try:
            import requests
            resp = requests.get(
                "https://api.dexscreener.com/latest/dex/search",
                params={"q": f"W{self.native_price_symbol} USDC"},
                timeout=10,
            )
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                for p in pairs:
                    base_sym = p.get("baseToken", {}).get("symbol", "").upper()
                    quote_sym = p.get("quoteToken", {}).get("symbol", "").upper()
                    if self.native_price_symbol in base_sym and ("USDC" in quote_sym or "USDT" in quote_sym):
                        return float(p.get("priceUsd", 0) or 0)
        except Exception:
            pass
        fallbacks = {"ETH": 3500.0, "BNB": 600.0}
        return fallbacks.get(self.native_price_symbol)

    def get_top_holders(self, contract_address: str, limit: int = 500) -> list[dict]:
        """Reconstruct holder balances from Transfer events."""
        print("   📊 Fetching transfer events to reconstruct holder balances...")
        transfers = self._fetch_all_transfers(contract_address)

        if not transfers:
            print("   ⚠️  No transfer events found.")
            return []

        print(f"   📊 Processing {len(transfers)} transfer events...")
        balances = {}
        total_supply = 0
        zero_addr = "0x0000000000000000000000000000000000000000"

        for tx in transfers:
            from_addr = tx.get("from", "").lower()
            to_addr = tx.get("to", "").lower()
            value = int(tx.get("value", "0"))

            if from_addr == zero_addr:
                total_supply += value
            if from_addr != zero_addr:
                balances[from_addr] = balances.get(from_addr, 0) - value
            if to_addr != zero_addr:
                balances[to_addr] = balances.get(to_addr, 0) + value

        holders = [
            {
                "address": addr,
                "balance": bal,
                "pct_supply": (bal / total_supply * 100) if total_supply > 0 else 0,
            }
            for addr, bal in balances.items()
            if bal > 0
        ]
        holders.sort(key=lambda x: x["balance"], reverse=True)
        return holders[:limit]

    def _fetch_all_transfers(self, contract_address: str, max_pages: int = 50) -> list[dict]:
        """Fetch all ERC20 Transfer events for a token, paginated."""
        all_transfers = []
        page = 1

        while page <= max_pages:
            data = rate_limited_get(
                self.explorer_base_url,
                params={
                    "module": "account",
                    "action": "tokentx",
                    "contractaddress": contract_address,
                    "page": page,
                    "offset": 10000,
                    "sort": "asc",
                },
                delay=self.api_delay,
            )
            if not data or data.get("status") != "1":
                break
            results = data.get("result", [])
            if not results:
                break
            all_transfers.extend(results)
            if len(results) < 10000:
                break
            page += 1
            if page % 5 == 0:
                print(f"   📊 Fetched {len(all_transfers)} transfers so far (page {page})...")

        return all_transfers

    def analyze_wallet(
        self,
        contract_address: str,
        wallet_address: str,
        current_price: float,
        token_decimals: int,
    ) -> Optional[dict]:
        """
        Analyze a wallet's trades by matching token transfers with
        ETH/stablecoin transfers in the same tx hash to derive real prices.
        
        API calls per wallet: up to 4
          1. tokentx (this token, this wallet)
          2. txlist (normal ETH txs)
          3. txlistinternal (internal ETH txs from router)
          4. tokentx (all ERC20 for this wallet — catches stablecoin side)
        """
        wallet_lower = wallet_address.lower()
        contract_lower = contract_address.lower()
        decimals_factor = 10 ** token_decimals

        # 1. Token transfers for this wallet + token
        token_data = rate_limited_get(
            self.explorer_base_url,
            params={
                "module": "account", "action": "tokentx",
                "contractaddress": contract_address,
                "address": wallet_address, "sort": "asc",
            },
            delay=self.api_delay,
        )
        if not token_data or token_data.get("status") != "1":
            return self._empty_analysis()
        token_txs = token_data.get("result", [])
        if not token_txs:
            return self._empty_analysis()

        # 2. Normal ETH/BNB transactions
        eth_data = rate_limited_get(
            self.explorer_base_url,
            params={
                "module": "account", "action": "txlist",
                "address": wallet_address, "sort": "asc",
            },
            delay=self.api_delay,
        )
        normal_txs = eth_data.get("result", []) if eth_data and eth_data.get("status") == "1" else []

        # 3. Internal transactions (routers return ETH via internal txs)
        internal_data = rate_limited_get(
            self.explorer_base_url,
            params={
                "module": "account", "action": "txlistinternal",
                "address": wallet_address, "sort": "asc",
            },
            delay=self.api_delay,
        )
        internal_txs = internal_data.get("result", []) if internal_data and internal_data.get("status") == "1" else []

        # 4. All ERC20 transfers (to catch USDC/WETH side of swaps)
        all_erc20_data = rate_limited_get(
            self.explorer_base_url,
            params={
                "module": "account", "action": "tokentx",
                "address": wallet_address, "sort": "asc",
            },
            delay=self.api_delay,
        )
        all_erc20_txs = all_erc20_data.get("result", []) if all_erc20_data and all_erc20_data.get("status") == "1" else []

        # ── Build hash → ETH value map ──────────────────────────────
        eth_by_hash = {}
        for tx in normal_txs:
            h = tx.get("hash", "").lower()
            eth_val = int(tx.get("value", "0")) / 1e18
            if eth_val > 0:
                if tx.get("from", "").lower() == wallet_lower:
                    eth_by_hash[h] = eth_by_hash.get(h, 0) - eth_val
                else:
                    eth_by_hash[h] = eth_by_hash.get(h, 0) + eth_val

        for tx in internal_txs:
            h = tx.get("hash", "").lower()
            eth_val = int(tx.get("value", "0")) / 1e18
            if eth_val > 0 and tx.get("to", "").lower() == wallet_lower:
                eth_by_hash[h] = eth_by_hash.get(h, 0) + eth_val

        # ── Build hash → stablecoin/WETH USD map ────────────────────
        paired_usd_by_hash = {}
        native_price = self._native_usd_price or 3500

        for tx in all_erc20_txs:
            token_addr = tx.get("contractAddress", "").lower()
            if token_addr == contract_lower:
                continue

            h = tx.get("hash", "").lower()
            tx_from = tx.get("from", "").lower()
            tx_to = tx.get("to", "").lower()
            raw_val = int(tx.get("value", "0"))
            dec = int(tx.get("tokenDecimal", "18"))
            val = raw_val / (10 ** dec)

            if token_addr in STABLECOIN_ADDRESSES:
                if tx_to == wallet_lower:
                    paired_usd_by_hash[h] = paired_usd_by_hash.get(h, 0) + val
                elif tx_from == wallet_lower:
                    paired_usd_by_hash[h] = paired_usd_by_hash.get(h, 0) - val

            elif token_addr in WETH_ADDRESSES:
                if tx_to == wallet_lower:
                    paired_usd_by_hash[h] = paired_usd_by_hash.get(h, 0) + (val * native_price)
                elif tx_from == wallet_lower:
                    paired_usd_by_hash[h] = paired_usd_by_hash.get(h, 0) - (val * native_price)

        # ── Process token txs and derive prices ─────────────────────
        buys = []   # (tokens, usd_cost, timestamp)
        sells = []  # (tokens, usd_revenue, timestamp)
        first_buy_time = None
        last_activity_time = None

        for tx in token_txs:
            h = tx.get("hash", "").lower()
            to_addr = tx.get("to", "").lower()
            from_addr = tx.get("from", "").lower()
            token_amount = int(tx.get("value", "0")) / decimals_factor
            timestamp = int(tx.get("timeStamp", "0"))

            if token_amount == 0:
                continue

            if last_activity_time is None or timestamp > last_activity_time:
                last_activity_time = timestamp

            # Derive USD value of the swap's other side
            usd_value = None
            if h in paired_usd_by_hash:
                usd_value = abs(paired_usd_by_hash[h])
            if usd_value is None and h in eth_by_hash:
                usd_value = abs(eth_by_hash[h]) * native_price

            if to_addr == wallet_lower:
                buys.append((token_amount, usd_value or 0, timestamp))
                if first_buy_time is None:
                    first_buy_time = timestamp
            elif from_addr == wallet_lower:
                sells.append((token_amount, usd_value or 0, timestamp))

        # ── Compute final metrics ────────────────────────────────────
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

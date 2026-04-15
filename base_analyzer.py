"""Base class for chain-specific analyzers."""

from abc import ABC, abstractmethod
from typing import Optional


class ChainAnalyzer(ABC):
    """Abstract base class that each chain analyzer must implement."""

    @abstractmethod
    def get_token_info(self, contract_address: str) -> Optional[dict]:
        """
        Fetch token metadata + current price.
        Returns dict with: name, symbol, decimals, price_usd,
                           liquidity_usd, volume_24h, market_cap
        """
        pass

    @abstractmethod
    def get_top_holders(self, contract_address: str, limit: int = 500) -> list[dict]:
        """
        Fetch top holders of a token.
        Returns list of dicts: [{ address, balance, pct_supply }, ...]
        """
        pass

    @abstractmethod
    def analyze_wallet(
        self,
        contract_address: str,
        wallet_address: str,
        current_price: float,
        token_decimals: int,
    ) -> Optional[dict]:
        """
        Analyze a single wallet's trade history for a specific token.
        Returns dict with:
            avg_entry_price, avg_exit_price, total_bought, total_sold,
            realized_pnl, unrealized_pnl, total_pnl, pnl_pct,
            first_buy_time, last_activity_time, num_buys, num_sells,
            is_in_profit (bool)
        """
        pass

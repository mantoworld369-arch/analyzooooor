"""Base chain analyzer using Basescan API."""

from chains.evm_analyzer import EVMAnalyzer


class BaseChainAnalyzer(EVMAnalyzer):
    """Analyzer for Base chain using Basescan (Etherscan-compatible) API."""

    explorer_base_url = "https://api.basescan.org/api"
    chain_id = "base"
    native_price_symbol = "ETH"
    api_delay = 0.25

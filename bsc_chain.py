"""BSC chain analyzer using BscScan API."""

from chains.evm_analyzer import EVMAnalyzer


class BSCChainAnalyzer(EVMAnalyzer):
    """Analyzer for BSC chain using BscScan (Etherscan-compatible) API."""

    explorer_base_url = "https://api.bscscan.com/api"
    chain_id = "bsc"
    native_price_symbol = "BNB"
    api_delay = 0.25

"""Utility functions for Analyzoooor."""

import re
import time
import requests
from typing import Optional


def print_banner():
    """Print the app banner."""
    banner = r"""
    ╔═══════════════════════════════════════════════════╗
    ║       _                _                          ║
    ║      / \   _ __   __ _| |_   _ _______   ___  _ __║
    ║     / _ \ | '_ \ / _` | | | | |_  / _ \ / _ \| '__|
    ║    / ___ \| | | | (_| | | |_| |/ / (_) | (_) | |  ║
    ║   /_/   \_\_| |_|\__,_|_|\__, /___\___/ \___/|_|  ║
    ║                          |___/                     ║
    ║                                                    ║
    ║   Token Holder Analytics · Base · BSC · Solana     ║
    ╚═══════════════════════════════════════════════════╝
    """
    print(banner)


def detect_chain(address: str) -> Optional[str]:
    """
    Auto-detect chain from address format.
    - Solana: base58, typically 32-44 chars, no 0x prefix
    - EVM (Base/BSC): 0x prefix, 42 chars hex
    For EVM, we can't distinguish Base vs BSC from address alone,
    so we try both explorers.
    """
    if address.startswith("0x") and len(address) == 42:
        # EVM address - try to detect via DexScreener
        chain = _detect_evm_chain(address)
        return chain
    elif re.match(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$', address):
        return "solana"
    return None


def _detect_evm_chain(address: str) -> Optional[str]:
    """Try DexScreener to figure out if a token is on Base or BSC."""
    try:
        resp = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{address}",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            pairs = data.get("pairs") or []
            for pair in pairs:
                chain_id = pair.get("chainId", "")
                if chain_id == "base":
                    return "base"
                elif chain_id == "bsc":
                    return "bsc"
    except Exception:
        pass
    return None


def rate_limited_get(url: str, params: dict = None, delay: float = 0.22,
                     max_retries: int = 3, timeout: int = 15) -> Optional[dict]:
    """
    Make a GET request with rate limiting and retries.
    Default delay of 0.22s = ~4.5 req/s (under 5/s free tier limit).
    """
    for attempt in range(max_retries):
        try:
            time.sleep(delay)
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"   ⏳ Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                return None
        except requests.RequestException:
            if attempt < max_retries - 1:
                time.sleep(1)
    return None


def get_dexscreener_price(contract: str) -> dict:
    """
    Fetch current token price + metadata from DexScreener (free, no key).
    Returns dict with price_usd, name, symbol, liquidity, volume_24h, etc.
    """
    try:
        resp = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{contract}",
            timeout=10,
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
        pairs = data.get("pairs") or []
        if not pairs:
            return {}

        # Use the highest-liquidity pair
        best = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
        return {
            "price_usd": float(best.get("priceUsd", 0) or 0),
            "name": best.get("baseToken", {}).get("name", "Unknown"),
            "symbol": best.get("baseToken", {}).get("symbol", "???"),
            "liquidity_usd": float(best.get("liquidity", {}).get("usd", 0) or 0),
            "volume_24h": float(best.get("volume", {}).get("h24", 0) or 0),
            "price_change_24h": float(best.get("priceChange", {}).get("h24", 0) or 0),
            "market_cap": float(best.get("marketCap", 0) or 0) if best.get("marketCap") else None,
            "pair_address": best.get("pairAddress", ""),
            "dex": best.get("dexId", ""),
            "chain_id": best.get("chainId", ""),
        }
    except Exception:
        return {}


def format_usd(value: float) -> str:
    """Format USD values nicely."""
    if abs(value) >= 1_000_000:
        return f"${value:,.0f}"
    elif abs(value) >= 1:
        return f"${value:,.2f}"
    elif abs(value) >= 0.001:
        return f"${value:.4f}"
    else:
        return f"${value:.10f}"


def format_pct(value: float) -> str:
    """Format percentage."""
    if value >= 0:
        return f"+{value:.1f}%"
    return f"{value:.1f}%"


def shorten_address(addr: str, chars: int = 6) -> str:
    """Shorten a wallet address for display."""
    if len(addr) <= chars * 2 + 3:
        return addr
    return f"{addr[:chars]}...{addr[-chars:]}"

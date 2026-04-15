#!/usr/bin/env python3
"""
Analyzoooor - Token Holder Analytics Tool
==========================================
Paste a contract address on Base, BSC, or Solana and get detailed
holder analytics: balances, PnL, entry/exit prices, and more.

Usage:
    python main.py
    python main.py <contract_address> --chain <base|bsc|solana>
"""

import argparse
import sys
import time
from chains.base_chain import BaseChainAnalyzer
from chains.bsc_chain import BSCChainAnalyzer
from chains.solana_chain import SolanaChainAnalyzer
from output_formatter import display_results, export_csv
from utils import detect_chain, print_banner


def get_analyzer(chain: str):
    analyzers = {
        "base": BaseChainAnalyzer,
        "bsc": BSCChainAnalyzer,
        "solana": SolanaChainAnalyzer,
    }
    cls = analyzers.get(chain)
    if not cls:
        print(f"[ERROR] Unknown chain: {chain}")
        sys.exit(1)
    return cls()


def main():
    print_banner()

    parser = argparse.ArgumentParser(description="Analyzoooor - Token Holder Analytics")
    parser.add_argument("contract", nargs="?", help="Token contract address")
    parser.add_argument("--chain", choices=["base", "bsc", "solana"], help="Blockchain (auto-detected if omitted)")
    parser.add_argument("--top", type=int, default=500, help="Number of top holders to analyze (default: 500)")
    parser.add_argument("--output", "-o", type=str, default=None, help="CSV output filename")
    args = parser.parse_args()

    if not args.contract:
        args.contract = input("\n📋 Paste contract address (CA): ").strip()
        if not args.contract:
            print("[ERROR] No contract address provided.")
            sys.exit(1)

    if not args.chain:
        detected = detect_chain(args.contract)
        if detected:
            print(f"\n🔗 Auto-detected chain: {detected.upper()}")
            args.chain = detected
        else:
            chain_input = input("\n🔗 Which chain? (base / bsc / solana): ").strip().lower()
            if chain_input not in ("base", "bsc", "solana"):
                print("[ERROR] Invalid chain selection.")
                sys.exit(1)
            args.chain = chain_input

    print(f"\n🔍 Analyzing token: {args.contract}")
    print(f"   Chain: {args.chain.upper()}")
    print(f"   Top holders: {args.top}")
    print(f"   {'─' * 50}")

    analyzer = get_analyzer(args.chain)

    # Step 1: Token info + price
    print("\n⏳ Fetching token info...")
    token_info = analyzer.get_token_info(args.contract)
    if not token_info:
        print("[ERROR] Could not fetch token info. Check the contract address.")
        sys.exit(1)

    print(f"   ✅ Token: {token_info.get('name', 'Unknown')} ({token_info.get('symbol', '?')})")
    print(f"   💰 Current price: ${token_info.get('price_usd', 0):.10f}")

    # Step 2: Top holders
    print(f"\n⏳ Fetching top {args.top} holders...")
    holders = analyzer.get_top_holders(args.contract, limit=args.top)
    if not holders:
        print("[ERROR] Could not fetch holder data.")
        sys.exit(1)
    print(f"   ✅ Found {len(holders)} holders")

    # Step 3: Analyze each wallet
    print(f"\n⏳ Analyzing trade history for {len(holders)} wallets...")
    print(f"   Deriving entry/exit prices from on-chain swap data")
    print(f"   (This takes a few minutes due to API rate limits)\n")

    results = []
    start_time = time.time()
    price_data_count = 0

    for i, holder in enumerate(holders):
        wallet = holder["address"]

        try:
            analysis = analyzer.analyze_wallet(
                contract_address=args.contract,
                wallet_address=wallet,
                current_price=token_info.get("price_usd", 0),
                token_decimals=token_info.get("decimals", 18),
            )
            if analysis:
                analysis["rank"] = i + 1
                analysis["address"] = wallet
                analysis["balance"] = holder.get("balance", 0)
                analysis["pct_supply"] = holder.get("pct_supply", 0)
                results.append(analysis)

                if analysis.get("total_cost_usd", 0) > 0:
                    price_data_count += 1
        except Exception as e:
            print(f"   [{i+1}/{len(holders)}] ⚠️  {wallet[:8]}... Error: {e}")
            continue

        if (i + 1) % 25 == 0 or (i + 1) == len(holders):
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            remaining = (len(holders) - i - 1) / rate if rate > 0 else 0
            print(
                f"   [{i+1}/{len(holders)}] ✅ {price_data_count} with price data"
                f" | ⏱️  {elapsed:.0f}s elapsed | ~{remaining:.0f}s remaining"
            )

    if not results:
        print("\n[ERROR] No wallet data could be retrieved.")
        sys.exit(1)

    # Step 4: Display
    print(f"\n{'═' * 60}")
    display_results(results, token_info)

    # Step 5: CSV
    csv_filename = args.output or f"analyzoooor_{token_info.get('symbol', 'TOKEN')}_{args.chain}.csv"
    export_csv(results, token_info, csv_filename)
    print(f"\n📁 CSV exported: {csv_filename}")
    print(f"\n✅ Done! Analyzed {len(results)} wallets, {price_data_count} with swap price data.")


if __name__ == "__main__":
    main()

"""
Output formatting: rich terminal display + CSV export.
"""

import csv
import datetime
from utils import format_usd, format_pct, shorten_address

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


def display_results(results: list[dict], token_info: dict):
    if HAS_RICH:
        _display_rich(results, token_info)
    else:
        _display_basic(results, token_info)


def _display_rich(results: list[dict], token_info: dict):
    console = Console()

    # ── Aggregate stats ──────────────────────────────────────────
    has_price_data = [r for r in results if r.get("total_cost_usd", 0) > 0]
    in_profit = sum(1 for r in has_price_data if r.get("is_in_profit", False))
    in_loss = len(has_price_data) - in_profit
    no_price = len(results) - len(has_price_data)
    diamond_hands = sum(1 for r in results if r.get("is_diamond_hands", False))
    total_holding_value = sum(r.get("holding_value_usd", 0) for r in results)

    # Average entry/exit for top 500
    entries_with_price = [r["avg_entry_price"] for r in has_price_data if r.get("avg_entry_price")]
    exits_with_price = [r["avg_exit_price"] for r in has_price_data if r.get("avg_exit_price")]
    avg_entry_top500 = sum(entries_with_price) / len(entries_with_price) if entries_with_price else None
    avg_exit_top500 = sum(exits_with_price) / len(exits_with_price) if exits_with_price else None

    total_pnl_sum = sum(r.get("total_pnl", 0) for r in has_price_data)

    # ── Summary panel ────────────────────────────────────────────
    summary = Text()
    summary.append(f"Token: ", style="bold")
    summary.append(f"{token_info.get('name', '?')} ({token_info.get('symbol', '?')})\n")
    summary.append(f"Price: ", style="bold")
    summary.append(f"{format_usd(token_info.get('price_usd', 0))}\n")
    pct_24h = token_info.get("price_change_24h", 0)
    summary.append(f"24h Change: ", style="bold")
    summary.append(f"{format_pct(pct_24h)}\n", style="green" if pct_24h >= 0 else "red")
    summary.append(f"Liquidity: ", style="bold")
    summary.append(f"{format_usd(token_info.get('liquidity_usd', 0))}\n")
    summary.append(f"24h Volume: ", style="bold")
    summary.append(f"{format_usd(token_info.get('volume_24h', 0))}\n")
    if token_info.get("market_cap"):
        summary.append(f"Market Cap: ", style="bold")
        summary.append(f"{format_usd(token_info['market_cap'])}\n")
    summary.append(f"\n")
    summary.append(f"Wallets Analyzed: ", style="bold")
    summary.append(f"{len(results)}\n")
    summary.append(f"With Price Data: ", style="bold")
    summary.append(f"{len(has_price_data)}")
    if no_price:
        summary.append(f" ({no_price} without swap data)\n", style="dim")
    else:
        summary.append(f"\n")
    summary.append(f"In Profit: ", style="bold")
    summary.append(f"{in_profit} ", style="green")
    if has_price_data:
        summary.append(f"({in_profit/len(has_price_data)*100:.1f}%)\n")
    else:
        summary.append(f"\n")
    summary.append(f"In Loss: ", style="bold")
    summary.append(f"{in_loss} ", style="red")
    if has_price_data:
        summary.append(f"({in_loss/len(has_price_data)*100:.1f}%)\n")
    else:
        summary.append(f"\n")
    summary.append(f"Diamond Hands: ", style="bold")
    summary.append(f"{diamond_hands}\n")
    summary.append(f"\n")
    summary.append(f"Top 500 Avg Entry: ", style="bold")
    summary.append(f"{format_usd(avg_entry_top500) if avg_entry_top500 else 'N/A'}\n")
    summary.append(f"Top 500 Avg Exit: ", style="bold")
    summary.append(f"{format_usd(avg_exit_top500) if avg_exit_top500 else 'N/A'}\n")
    summary.append(f"Top 500 Aggregate PnL: ", style="bold")
    pnl_style = "green" if total_pnl_sum >= 0 else "red"
    summary.append(f"{format_usd(total_pnl_sum)}\n", style=pnl_style)
    summary.append(f"Top 500 Holding Value: ", style="bold")
    summary.append(f"{format_usd(total_holding_value)}\n")

    console.print(Panel(summary, title="📊 ANALYZOOOOR SUMMARY", border_style="cyan", expand=False))

    # ── Holder table ─────────────────────────────────────────────
    table = Table(
        title="Top Holders Analysis",
        box=box.ROUNDED,
        show_lines=False,
        header_style="bold cyan",
    )

    table.add_column("#", justify="right", style="dim", width=5)
    table.add_column("Wallet", style="bright_white", width=15)
    table.add_column("Value", justify="right", width=12)
    table.add_column("% Supply", justify="right", width=7)
    table.add_column("Entry", justify="right", width=12)
    table.add_column("Exit", justify="right", width=12)
    table.add_column("PnL", justify="right", width=12)
    table.add_column("PnL%", justify="right", width=8)
    table.add_column("B/S", justify="center", width=6)
    table.add_column("Type", justify="center", width=8)

    display_count = min(50, len(results))
    for r in results[:display_count]:
        rank = str(r.get("rank", ""))
        wallet = shorten_address(r.get("address", ""), 5)
        value = format_usd(r.get("holding_value_usd", 0))
        pct = f"{r.get('pct_supply', 0):.2f}%"

        entry = format_usd(r["avg_entry_price"]) if r.get("avg_entry_price") else "—"
        exit_p = format_usd(r["avg_exit_price"]) if r.get("avg_exit_price") else "—"

        pnl_val = r.get("total_pnl", 0)
        if r.get("total_cost_usd", 0) > 0:
            pnl_str = format_usd(pnl_val)
            pnl_style = "green" if pnl_val >= 0 else "red"
            pnl_text = Text(pnl_str, style=pnl_style)
            pnl_pct_text = Text(format_pct(r.get("pnl_pct", 0)), style=pnl_style)
        else:
            pnl_text = Text("—", style="dim")
            pnl_pct_text = Text("—", style="dim")

        bs = f"{r.get('num_buys', 0)}/{r.get('num_sells', 0)}"

        if r.get("is_diamond_hands"):
            wtype = Text("💎", style="bright_cyan")
        elif r.get("num_sells", 0) > r.get("num_buys", 0):
            wtype = Text("📤", style="yellow")
        else:
            wtype = Text("🔄", style="white")

        table.add_row(rank, wallet, value, pct, entry, exit_p, pnl_text, pnl_pct_text, bs, wtype)

    console.print(table)
    if len(results) > display_count:
        console.print(f"\n   [dim](Showing top {display_count} of {len(results)}. Full data in CSV.)[/dim]")


def _display_basic(results: list[dict], token_info: dict):
    has_price_data = [r for r in results if r.get("total_cost_usd", 0) > 0]
    in_profit = sum(1 for r in has_price_data if r.get("is_in_profit", False))
    in_loss = len(has_price_data) - in_profit
    diamond_hands = sum(1 for r in results if r.get("is_diamond_hands", False))

    print(f"\n{'═' * 80}")
    print(f"  ANALYZOOOOR SUMMARY")
    print(f"{'═' * 80}")
    print(f"  Token: {token_info.get('name', '?')} ({token_info.get('symbol', '?')})")
    print(f"  Price: {format_usd(token_info.get('price_usd', 0))}")
    print(f"  Wallets: {len(results)} | With price data: {len(has_price_data)}")
    print(f"  In Profit: {in_profit} | In Loss: {in_loss} | Diamond Hands: {diamond_hands}")
    print(f"{'═' * 80}")

    fmt = "{:<5} {:<14} {:>11} {:>7} {:>11} {:>11} {:>11} {:>8} {:>5}"
    print(fmt.format("#", "Wallet", "Value", "%Sup", "Entry", "Exit", "PnL", "PnL%", "B/S"))
    print(f"{'─' * 80}")

    display_count = min(50, len(results))
    for r in results[:display_count]:
        entry = format_usd(r["avg_entry_price"]) if r.get("avg_entry_price") else "—"
        exit_p = format_usd(r["avg_exit_price"]) if r.get("avg_exit_price") else "—"
        pnl = format_usd(r.get("total_pnl", 0)) if r.get("total_cost_usd", 0) > 0 else "—"
        pnl_pct = format_pct(r.get("pnl_pct", 0)) if r.get("total_cost_usd", 0) > 0 else "—"
        bs = f"{r.get('num_buys', 0)}/{r.get('num_sells', 0)}"

        print(fmt.format(
            r.get("rank", ""),
            shorten_address(r.get("address", ""), 5),
            format_usd(r.get("holding_value_usd", 0)),
            f"{r.get('pct_supply', 0):.2f}%",
            entry, exit_p, pnl, pnl_pct, bs,
        ))


def export_csv(results: list[dict], token_info: dict, filename: str):
    fieldnames = [
        "rank", "wallet_address", "wallet_short",
        "token_balance", "pct_supply", "holding_value_usd",
        "avg_entry_price", "avg_exit_price",
        "total_cost_usd", "total_revenue_usd",
        "realized_pnl", "unrealized_pnl", "total_pnl", "pnl_pct",
        "num_buys", "num_sells", "num_buys_with_price", "num_sells_with_price",
        "total_tokens_bought", "total_tokens_sold",
        "is_in_profit", "is_diamond_hands",
        "first_buy_timestamp", "first_buy_date",
        "last_activity_timestamp", "last_activity_date",
    ]

    with open(filename, "w", newline="") as f:
        f.write(f"# Token: {token_info.get('name', '?')} ({token_info.get('symbol', '?')})\n")
        f.write(f"# Price: {token_info.get('price_usd', 0)}\n")
        f.write(f"# Generated: {datetime.datetime.now().isoformat()}\n")
        f.write(f"# Wallets: {len(results)}\n")

        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for r in results:
            first_ts = r.get("first_buy_time")
            last_ts = r.get("last_activity_time")

            writer.writerow({
                "rank": r.get("rank", ""),
                "wallet_address": r.get("address", ""),
                "wallet_short": shorten_address(r.get("address", "")),
                "token_balance": r.get("current_holding", 0),
                "pct_supply": r.get("pct_supply", 0),
                "holding_value_usd": r.get("holding_value_usd", 0),
                "avg_entry_price": r.get("avg_entry_price") or "",
                "avg_exit_price": r.get("avg_exit_price") or "",
                "total_cost_usd": r.get("total_cost_usd", 0),
                "total_revenue_usd": r.get("total_revenue_usd", 0),
                "realized_pnl": r.get("realized_pnl", 0),
                "unrealized_pnl": r.get("unrealized_pnl", 0),
                "total_pnl": r.get("total_pnl", 0),
                "pnl_pct": r.get("pnl_pct", 0),
                "num_buys": r.get("num_buys", 0),
                "num_sells": r.get("num_sells", 0),
                "num_buys_with_price": r.get("num_buys_with_price", 0),
                "num_sells_with_price": r.get("num_sells_with_price", 0),
                "total_tokens_bought": r.get("total_bought", 0),
                "total_tokens_sold": r.get("total_sold", 0),
                "is_in_profit": r.get("is_in_profit", False),
                "is_diamond_hands": r.get("is_diamond_hands", False),
                "first_buy_timestamp": first_ts or "",
                "first_buy_date": datetime.datetime.fromtimestamp(first_ts).isoformat() if first_ts else "",
                "last_activity_timestamp": last_ts or "",
                "last_activity_date": datetime.datetime.fromtimestamp(last_ts).isoformat() if last_ts else "",
            })

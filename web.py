#!/usr/bin/env python3
"""
Analyzoooor Web Server
======================
Flask app that serves the HTML frontend and runs analysis via API.
Uses Server-Sent Events (SSE) to stream progress to the browser in real time.

Usage:
    python web.py
    python web.py --port 8080
    python web.py --host 0.0.0.0 --port 5000   # expose to network
"""

import argparse
import json
import os
import queue
import threading
import time
import uuid

from flask import Flask, Response, jsonify, request, send_from_directory

# Import analyzers (flat structure — no chains/ subfolder)
from base_chain import BaseChainAnalyzer
from bsc_chain import BSCChainAnalyzer
from solana_chain import SolanaChainAnalyzer
from utils import detect_chain, get_dexscreener_price, format_usd, shorten_address

app = Flask(__name__, static_folder="static")

# Store running jobs: job_id -> { queue, results, status, ... }
jobs = {}


def get_analyzer(chain: str):
    return {
        "base": BaseChainAnalyzer,
        "bsc": BSCChainAnalyzer,
        "solana": SolanaChainAnalyzer,
    }.get(chain, lambda: None)()


# ── Routes ───────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/detect", methods=["POST"])
def api_detect():
    """Auto-detect chain from contract address."""
    data = request.json or {}
    ca = data.get("contract", "").strip()
    if not ca:
        return jsonify({"error": "No contract address"}), 400
    chain = detect_chain(ca)
    return jsonify({"chain": chain})


@app.route("/api/token-info", methods=["POST"])
def api_token_info():
    """Fetch token info without running full analysis."""
    data = request.json or {}
    ca = data.get("contract", "").strip()
    if not ca:
        return jsonify({"error": "No contract address"}), 400
    info = get_dexscreener_price(ca)
    if not info:
        return jsonify({"error": "Token not found on DexScreener"}), 404
    return jsonify(info)


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """Start an analysis job. Returns job_id for SSE streaming."""
    data = request.json or {}
    ca = data.get("contract", "").strip()
    chain = data.get("chain", "").strip().lower()
    top = min(int(data.get("top", 500)), 500)

    if not ca:
        return jsonify({"error": "No contract address"}), 400
    if chain not in ("base", "bsc", "solana"):
        return jsonify({"error": "Invalid chain"}), 400

    job_id = str(uuid.uuid4())[:8]
    q = queue.Queue()
    jobs[job_id] = {"queue": q, "results": [], "token_info": {}, "status": "running"}

    thread = threading.Thread(target=_run_analysis, args=(job_id, ca, chain, top), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/stream/<job_id>")
def api_stream(job_id):
    """SSE endpoint — streams progress events to the browser."""
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    def generate():
        while True:
            try:
                msg = job["queue"].get(timeout=120)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("type") in ("done", "error"):
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/results/<job_id>")
def api_results(job_id):
    """Get final results for a completed job."""
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "token_info": job.get("token_info", {}),
        "results": job.get("results", []),
        "status": job.get("status", "unknown"),
    })


# ── Analysis worker ──────────────────────────────────────────────

def _run_analysis(job_id, contract, chain, top):
    job = jobs[job_id]
    q = job["queue"]

    def emit(msg):
        q.put(msg)

    try:
        emit({"type": "status", "msg": "Initializing analyzer..."})

        analyzer = get_analyzer(chain)
        if not analyzer:
            emit({"type": "error", "msg": f"Unknown chain: {chain}"})
            return

        # Step 1: Token info
        emit({"type": "status", "msg": "Fetching token info from DexScreener..."})
        token_info = analyzer.get_token_info(contract)
        if not token_info:
            emit({"type": "error", "msg": "Could not fetch token info. Check the CA."})
            return

        job["token_info"] = token_info
        emit({
            "type": "token_info",
            "data": token_info,
        })

        # Step 2: Top holders
        emit({"type": "status", "msg": f"Fetching top {top} holders..."})
        holders = analyzer.get_top_holders(contract, limit=top)
        if not holders:
            emit({"type": "error", "msg": "Could not fetch holder data."})
            return

        emit({"type": "status", "msg": f"Found {len(holders)} holders. Analyzing wallets..."})
        emit({"type": "holders_count", "count": len(holders)})

        # Step 3: Analyze each wallet
        results = []
        price_data_count = 0
        start_time = time.time()

        for i, holder in enumerate(holders):
            wallet = holder["address"]
            try:
                analysis = analyzer.analyze_wallet(
                    contract_address=contract,
                    wallet_address=wallet,
                    current_price=token_info.get("price_usd", 0),
                    token_decimals=token_info.get("decimals", 18),
                )
                if analysis:
                    analysis["rank"] = i + 1
                    analysis["address"] = wallet
                    analysis["address_short"] = shorten_address(wallet, 5)
                    analysis["balance"] = holder.get("balance", 0)
                    analysis["pct_supply"] = holder.get("pct_supply", 0)
                    results.append(analysis)

                    if analysis.get("total_cost_usd", 0) > 0:
                        price_data_count += 1

                    # Stream each result to UI
                    emit({"type": "wallet_result", "data": analysis})

            except Exception as e:
                emit({"type": "wallet_error", "wallet": wallet[:10], "error": str(e)})

            # Progress update
            if (i + 1) % 5 == 0 or (i + 1) == len(holders):
                elapsed = time.time() - start_time
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                remaining = (len(holders) - i - 1) / rate if rate > 0 else 0
                emit({
                    "type": "progress",
                    "current": i + 1,
                    "total": len(holders),
                    "with_price": price_data_count,
                    "elapsed": round(elapsed),
                    "remaining": round(remaining),
                })

        job["results"] = results
        job["status"] = "done"

        # Compute summary
        has_price = [r for r in results if r.get("total_cost_usd", 0) > 0]
        in_profit = sum(1 for r in has_price if r.get("is_in_profit"))
        entries = [r["avg_entry_price"] for r in has_price if r.get("avg_entry_price")]
        exits = [r["avg_exit_price"] for r in has_price if r.get("avg_exit_price")]

        emit({
            "type": "done",
            "summary": {
                "total_wallets": len(results),
                "with_price_data": len(has_price),
                "in_profit": in_profit,
                "in_loss": len(has_price) - in_profit,
                "diamond_hands": sum(1 for r in results if r.get("is_diamond_hands")),
                "avg_entry_top500": sum(entries) / len(entries) if entries else None,
                "avg_exit_top500": sum(exits) / len(exits) if exits else None,
                "total_pnl": sum(r.get("total_pnl", 0) for r in has_price),
                "total_holding_value": sum(r.get("holding_value_usd", 0) for r in results),
            },
        })

    except Exception as e:
        emit({"type": "error", "msg": str(e)})
        job["status"] = "error"


# ── Main ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    os.makedirs("static", exist_ok=True)
    print(f"\n🚀 Analyzoooor Web UI running at http://{args.host}:{args.port}\n")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)

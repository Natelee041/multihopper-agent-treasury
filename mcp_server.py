#!/usr/bin/env python3
"""
MultiHopper Agent Treasury — Model Context Protocol (MCP) Server (JSON-RPC 2.0 over stdio)
Exposes MultiHopper v1 Solana routed transfer tools to MCP-compatible AI agents
(Cursor, Claude Desktop, Claude Code, Grok Bot).
"""
import json
import os
import sys
from typing import Any

from multihopper_agent_treasury import (
    SOL_MINT,
    USDC_MINT,
    USDG_MINT,
    AgentTreasuryPolicy,
    MultiHopperClient,
    verify_webhook_signature,
)

TOOLS = [
    {
        "name": "multihopper_estimate_fees",
        "description": "Quote MultiHopper protocol & integrator fees (0.25% revenue share) plus TRM compliance screening costs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token_mint": {"type": "string", "default": USDC_MINT},
                "amount_raw": {"type": "integer"},
                "token_decimals": {"type": "integer", "default": 6},
                "hops": {"type": "integer", "default": 7},
                "environment": {"type": "string", "enum": ["devnet", "mainnet"], "default": "devnet"},
            },
            "required": ["amount_raw"],
        },
    },
    {
        "name": "multihopper_create_routed_transfer",
        "description": "Create a policy-guarded single-recipient or multi-destination (up to 20 wallets) MultiHopper transfer quote.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_owner": {"type": "string"},
                "token_mint": {"type": "string", "default": USDC_MINT},
                "amount_raw": {"type": "integer"},
                "amount_tokens": {"type": "string"},
                "token_decimals": {"type": "integer", "default": 6},
                "token_symbol": {"type": "string", "default": "USDC"},
                "recipient_wallet": {"type": "string"},
                "destinations": {"type": "array"},
                "hops": {"type": "integer", "default": 7},
                "arrival_seconds": {"type": "integer", "default": 300},
                "environment": {"type": "string", "enum": ["devnet", "mainnet"], "default": "devnet"},
            },
            "required": ["source_owner", "amount_raw", "amount_tokens"],
        },
    },
    {
        "name": "multihopper_verify_webhook",
        "description": "Verify MultiHopper HMAC-SHA256 webhook signature (x-multihopper-signature).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "payload": {"type": "string"},
                "signature": {"type": "string"},
                "secret": {"type": "string"},
            },
            "required": ["payload", "signature", "secret"],
        },
    },
]


def handle_rpc_request(req: dict[str, Any]) -> dict[str, Any] | None:
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "multihopper-agent-treasury-mcp", "version": "1.0.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            env = args.get("environment", "devnet")
            client = MultiHopperClient(
                api_key=os.environ.get("MULTIHOPPER_API_KEY", "mh_test_dryrun_key"),
                environment=env,
                dry_run=True,
            )
            if name == "multihopper_estimate_fees":
                out = client.estimate_fees(
                    token_mint=args.get("token_mint", USDC_MINT),
                    amount_raw=args["amount_raw"],
                    token_decimals=args.get("token_decimals", 6),
                    hops=args.get("hops", 7),
                )
            elif name == "multihopper_create_routed_transfer":
                out = client.create_transfer(
                    source_owner=args["source_owner"],
                    token_mint=args.get("token_mint", USDC_MINT),
                    amount_raw=args["amount_raw"],
                    amount_tokens=args["amount_tokens"],
                    token_decimals=args.get("token_decimals", 6),
                    token_symbol=args.get("token_symbol", "USDC"),
                    recipient_wallet=args.get("recipient_wallet"),
                    destinations=args.get("destinations"),
                    hops=args.get("hops", 7),
                    arrival_seconds=args.get("arrival_seconds", 300),
                )
            elif name == "multihopper_verify_webhook":
                valid = verify_webhook_signature(args["payload"], args["signature"], args["secret"])
                out = {"valid": valid}
            else:
                raise ValueError(f"Unknown tool: {name}")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(out, indent=2)}], "isError": False},
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": str(e)}], "isError": True},
            }
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        resp = handle_rpc_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

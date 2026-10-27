#!/usr/bin/env python3
"""Unit tests for MultiHopper Agent Treasury SDK, Policy Engine, Webhook Verifier & MCP Server."""
import hashlib
import hmac
import json
import unittest

from mcp_server import handle_rpc_request
from multihopper_agent_treasury import (
    SOL_MINT,
    USDC_MINT,
    AgentTreasuryPolicy,
    MultiHopperClient,
    verify_webhook_signature,
)


class TestMultiHopperAgentTreasury(unittest.TestCase):
    def setUp(self):
        self.client = MultiHopperClient(environment="devnet", dry_run=True)
        self.wallet = "St9fjc5egj6rufgBhFLoN5iDzzUgtD8jnTnwtdz5poQ"

    def test_estimate_fees_and_integrator_share(self):
        res = self.client.estimate_fees(USDC_MINT, 100_000_000, token_decimals=6, hops=7)
        self.assertEqual(res["percentFeeBps"], 50)
        self.assertEqual(res["integratorRevenueShareBps"], 25)
        self.assertEqual(res["integratorFeeEarnedRaw"], "250000")  # 0.25 USDC on 100 USDC

    def test_single_recipient_transfer_and_two_stage_confirm(self):
        tx = self.client.create_transfer(
            source_owner=self.wallet,
            token_mint=SOL_MINT,
            amount_raw=1_000_000_000,
            amount_tokens="1.0",
            token_decimals=9,
            token_symbol="SOL",
            recipient_wallet="7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
            hops=5,
        )
        self.assertEqual(tx["status"], "awaiting_signature")
        self.assertEqual(tx["policyVerification"]["screened_wallets_count"], 2)

        stage1 = self.client.confirm_broadcast(tx["id"], keeper_funding_signature="sig_keeper_1")
        self.assertEqual(stage1["status"], "keeper_funded_awaiting_route_sigs")

        stage2 = self.client.confirm_broadcast(
            tx["id"],
            keeper_funding_signature="sig_keeper_1",
            route_init_signatures=["sig_route_1"],
            orchestrator_init_signature="sig_orch_1",
            session_init_signatures=["sig_sess_1"],
        )
        self.assertEqual(stage2["status"], "processing")

    def test_multi_destination_validation(self):
        # Mismatched share sum must raise ValueError
        with self.assertRaises(ValueError):
            self.client.create_transfer(
                source_owner=self.wallet,
                token_mint=USDC_MINT,
                amount_raw=100_000_000,
                amount_tokens="100.0",
                destinations=[
                    {"wallet": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "amountRaw": "50000000"},
                    {"wallet": "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin", "amountRaw": "40000000"},
                ],
            )
        # Duplicate destination wallets must raise ValueError
        with self.assertRaises(ValueError):
            self.client.create_transfer(
                source_owner=self.wallet,
                token_mint=USDC_MINT,
                amount_raw=100_000_000,
                amount_tokens="100.0",
                destinations=[
                    {"wallet": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "amountRaw": "50000000"},
                    {"wallet": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "amountRaw": "50000000"},
                ],
            )

    def test_spend_cap_policy_blocks_excess_and_live_mode(self):
        with self.assertRaises(ValueError):
            self.client.create_transfer(
                source_owner=self.wallet,
                token_mint=USDC_MINT,
                amount_raw=999_000_000_000,  # Exceeds 500 USDC cap
                amount_tokens="999000.0",
                recipient_wallet="7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
            )
        # Live mode blocked by default policy
        live_client = MultiHopperClient(environment="devnet", dry_run=False)
        with self.assertRaises(ValueError):
            live_client.create_transfer(
                source_owner=self.wallet,
                token_mint=SOL_MINT,
                amount_raw=100_000_000,
                amount_tokens="0.1",
                recipient_wallet="7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
            )

    def test_webhook_hmac_verification(self):
        secret = "whsec_test_secret_123"
        payload = '{"event":"transfer.completed","transferId":"42"}'
        sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        self.assertTrue(verify_webhook_signature(payload, sig, secret))
        self.assertFalse(verify_webhook_signature(payload, "deadbeef", secret))

    def test_mcp_server_rpc(self):
        init_resp = handle_rpc_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(init_resp["result"]["serverInfo"]["name"], "multihopper-agent-treasury-mcp")

        call_resp = handle_rpc_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "multihopper_estimate_fees",
                "arguments": {"amount_raw": 50_000_000, "hops": 6},
            },
        })
        self.assertFalse(call_resp["result"]["isError"])
        parsed = json.loads(call_resp["result"]["content"][0]["text"])
        self.assertEqual(parsed["hops"], 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)

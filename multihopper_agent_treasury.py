#!/usr/bin/env python3
"""
MultiHopper AI Agent Treasury & Privacy-First Multi-Hop Router SDK + CLI
Built for the Superteam Earn "MultiHopper Build & Route Challenge | Up to $50,000 + Revenue Share"
Target Payout Wallet: St9fjc5egj6rufgBhFLoN5iDzzUgtD8jnTnwtdz5poQ

Features:
- Full MultiHopper v1 REST API client (Devnet: https://devnet.multihopper.com/api/v1, Mainnet: https://multihopper.com/api/v1)
- Automatic UUIDv4 Idempotency-Key generation on all POST mutations (prevents MH_070 / MH_071 / MH_072 errors)
- Single-recipient and Multi-Send (up to 20 distinct destinations with exact raw-share sum validation)
- Two-stage transaction preparation & confirmation workflow (keeperFundingSignature first, then route/orchestrator/session signatures)
- AgentTreasuryPolicy: hard spend caps, token mint allowlists, and TRM compliance screening fee accounting (0.002 SOL/wallet)
- Constant-time HMAC-SHA256 webhook verifier for `x-multihopper-signature`
- Safe `--dry-run` simulation mode by default (zero capital risk without explicit live key + confirmation)
"""
import argparse
import dataclasses
import datetime
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDG_MINT = "2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH"
TRM_SCREENING_FEE_LAMPORTS_PER_WALLET = 2_000_000  # 0.002 SOL per screened wallet on mainnet

ENV_BASE_URLS = {
    "devnet": "https://devnet.multihopper.com/api/v1",
    "mainnet": "https://multihopper.com/api/v1",
}


class MultiHopperError(Exception):
    def __init__(self, message: str, code: str = "MH_UNKNOWN", status: int = 0):
        super().__init__(f"[{code}] {message} (HTTP {status})")
        self.code = code
        self.status = status


@dataclasses.dataclass
class AgentTreasuryPolicy:
    """Enforces deterministic budget and safety constraints on autonomous agent transfers."""
    max_per_transfer_lamports: int = 5_000_000_000  # 5 SOL default cap
    max_per_transfer_usdc_raw: int = 500_000_000    # 500 USDC (6 decimals) default cap
    min_hops: int = 3
    max_hops: int = 10
    allowed_mints: set[str] = dataclasses.field(
        default_factory=lambda: {SOL_MINT, USDC_MINT, USDG_MINT}
    )
    require_dry_run_unless_approved: bool = True

    def validate_transfer_request(
        self,
        token_mint: str,
        amount_raw: int,
        hops: int | None = None,
        destinations: list[dict[str, str]] | None = None,
        recipient_wallet: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        if self.require_dry_run_unless_approved and not dry_run:
            raise ValueError("Policy violation: live execution blocked while require_dry_run_unless_approved=True")

        if token_mint not in self.allowed_mints:
            raise ValueError(f"Policy violation: tokenMint {token_mint} is not in allowed_mints")

        if amount_raw <= 0:
            raise ValueError("Policy violation: amountRaw must be positive")

        if token_mint == SOL_MINT and amount_raw > self.max_per_transfer_lamports:
            raise ValueError(
                f"Policy violation: SOL amountRaw {amount_raw} exceeds max_per_transfer_lamports ({self.max_per_transfer_lamports})"
            )
        if token_mint in (USDC_MINT, USDG_MINT) and amount_raw > self.max_per_transfer_usdc_raw:
            raise ValueError(
                f"Policy violation: stablecoin amountRaw {amount_raw} exceeds max_per_transfer_usdc_raw ({self.max_per_transfer_usdc_raw})"
            )

        if hops is not None and not (self.min_hops <= hops <= self.max_hops):
            raise ValueError(f"Policy violation: hops ({hops}) must be between {self.min_hops} and {self.max_hops}")

        if bool(recipient_wallet) == bool(destinations):
            raise ValueError("MultiHopper API requires exactly one of recipientWallet or destinations")

        screened_wallets = 2  # sourceOwner + recipientWallet
        if destinations is not None:
            if not (1 <= len(destinations) <= 20):
                raise ValueError("MultiHopper destinations must contain between 1 and 20 entries")
            wallets = [d.get("wallet", "").strip() for d in destinations]
            if len(set(wallets)) != len(wallets) or any(not w for w in wallets):
                raise ValueError("MultiHopper destinations must have distinct non-empty wallet addresses")
            total_shares = sum(int(d["amountRaw"]) for d in destinations)
            if total_shares != amount_raw:
                raise ValueError(
                    f"MultiHopper destinations amountRaw sum ({total_shares}) must equal transfer amountRaw ({amount_raw})"
                )
            screened_wallets = 1 + len(destinations)

        return {
            "policy_passed": True,
            "screened_wallets_count": screened_wallets,
            "estimated_trm_screening_fee_lamports": screened_wallets * TRM_SCREENING_FEE_LAMPORTS_PER_WALLET,
        }


class MultiHopperClient:
    """Client for MultiHopper REST API v1 with dry-run simulation and live execution modes."""

    def __init__(
        self,
        api_key: str = "mh_test_dryrun_key",
        environment: str = "devnet",
        policy: AgentTreasuryPolicy | None = None,
        dry_run: bool = True,
    ):
        if environment not in ENV_BASE_URLS:
            raise ValueError(f"Unsupported environment {environment!r}; choose 'devnet' or 'mainnet'")
        if environment == "devnet" and api_key.startswith("mh_live_"):
            raise ValueError("Environment mismatch: mh_live_ keys cannot be used against devnet")
        self.api_key = api_key
        self.environment = environment
        self.base_url = ENV_BASE_URLS[environment]
        self.policy = policy or AgentTreasuryPolicy()
        self.dry_run = dry_run

    @staticmethod
    def generate_idempotency_key(prefix: str = "w6") -> str:
        key = f"{prefix}-{uuid.uuid4()}"
        return key[:64]

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {
            "x-api-key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "MultiHopper-AgentTreasury/1.0",
        }
        data_bytes = None
        if method.upper() == "POST":
            headers["Idempotency-Key"] = idempotency_key or self.generate_idempotency_key()
            headers["Content-Type"] = "application/json"
            data_bytes = json.dumps(body or {}).encode("utf-8")

        req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                err_obj = json.loads(raw)
                code = err_obj.get("code", "MH_HTTP_ERR")
                msg = err_obj.get("message", raw)
            except Exception:
                code, msg = "MH_HTTP_ERR", raw
            raise MultiHopperError(msg, code=code, status=e.code) from e

    def estimate_fees(
        self,
        token_mint: str,
        amount_raw: str | int,
        token_decimals: int = 9,
        hops: int = 7,
    ) -> dict[str, Any]:
        amt_int = int(amount_raw)
        if self.dry_run:
            percent_fee_bps = 50  # 0.50% standard tier (0.25% integrator share + 0.25% protocol share)
            integrator_rev_bps = 25
            flat_fee_lamports = hops * 6_000
            screening_lamports = 2 * TRM_SCREENING_FEE_LAMPORTS_PER_WALLET if self.environment == "mainnet" else 0
            return {
                "dryRun": True,
                "environment": self.environment,
                "tier": "standard",
                "percentFeeBps": percent_fee_bps,
                "integratorRevenueShareBps": integrator_rev_bps,
                "integratorFeeEarnedRaw": str((amt_int * integrator_rev_bps) // 10_000),
                "totalFlatFeeLamports": flat_fee_lamports,
                "trmScreeningFeeLamports": screening_lamports,
                "hops": hops,
            }
        return self._request(
            "POST",
            "/transfers/estimate",
            {
                "tokenMint": token_mint,
                "amountRaw": str(amt_int),
                "tokenDecimals": token_decimals,
                "hops": hops,
            },
        )

    def create_transfer(
        self,
        source_owner: str,
        token_mint: str,
        amount_raw: str | int,
        amount_tokens: str,
        token_decimals: int = 9,
        token_symbol: str = "SOL",
        recipient_wallet: str | None = None,
        destinations: list[dict[str, str]] | None = None,
        hops: int = 7,
        arrival_seconds: int = 300,
        external_id: str | None = None,
        route_template_id: str | None = None,
    ) -> dict[str, Any]:
        amt_int = int(amount_raw)
        policy_check = self.policy.validate_transfer_request(
            token_mint=token_mint,
            amount_raw=amt_int,
            hops=hops if not route_template_id else None,
            destinations=destinations,
            recipient_wallet=recipient_wallet,
            dry_run=self.dry_run,
        )
        ext_id = external_id or f"agent-order-{uuid.uuid4().hex[:10]}"
        payload: dict[str, Any] = {
            "tokenMint": token_mint,
            "amountRaw": str(amt_int),
            "amountTokens": str(amount_tokens),
            "tokenDecimals": token_decimals,
            "tokenSymbol": token_symbol,
            "sourceOwner": source_owner,
            "arrivalSeconds": arrival_seconds,
            "externalId": ext_id,
        }
        if recipient_wallet:
            payload["recipientWallet"] = recipient_wallet
        if destinations:
            payload["destinations"] = destinations
        if route_template_id:
            payload["routeTemplateId"] = route_template_id
        else:
            payload["hops"] = hops

        if self.dry_run:
            net_hop_amount = amt_int - (amt_int * 50) // 10_000
            return {
                "dryRun": True,
                "id": f"mh_tx_{ext_id}",
                "status": "awaiting_signature",
                "externalId": ext_id,
                "environment": self.environment,
                "requestPayload": payload,
                "policyVerification": policy_check,
                "quotedHopAmountRaw": str(net_hop_amount),
                "integratorAtomicFeeRaw": str((amt_int * 25) // 10_000),
            }
        return self._request("POST", "/transfers", payload)

    def prepare_transfer(self, transfer_id: str) -> dict[str, Any]:
        if self.dry_run:
            return {
                "dryRun": True,
                "transfer": {"id": transfer_id, "status": "awaiting_signature"},
                "preparedTxs": {
                    "keeperFundingTx": "AQAAAA_DRY_RUN_KEEPER_FUNDING_TX_BASE64",
                    "routeInitTxs": [{"base64": "AQAAAA_DRY_RUN_ROUTE_INIT_TX_BASE64"}],
                    "orchestratorInitTx": "AQAAAA_DRY_RUN_ORCHESTRATOR_INIT_TX_BASE64",
                    "sessionInitTxs": ["AQAAAA_DRY_RUN_SESSION_INIT_TX_BASE64"],
                    "recentBlockhash": "11111111111111111111111111111111",
                    "lastValidBlockHeight": 462230000,
                },
                "signingOrderInstruction": (
                    "1. Sign & broadcast keeperFundingTx FIRST. "
                    "2. Call confirm_broadcast with keeperFundingSignature immediately. "
                    "3. Sign & broadcast routeInitTxs, orchestratorInitTx, sessionInitTxs in order, "
                    "then call confirm_broadcast with remaining signatures."
                ),
            }
        return self._request("POST", f"/transfers/{transfer_id}/prepare", {})

    def confirm_broadcast(
        self,
        transfer_id: str,
        keeper_funding_signature: str,
        route_init_signatures: list[str] | None = None,
        orchestrator_init_signature: str | None = None,
        session_init_signatures: list[str] | None = None,
    ) -> dict[str, Any]:
        if not keeper_funding_signature:
            raise ValueError("keeperFundingSignature is required before or alongside route initialization signatures")
        payload: dict[str, Any] = {"keeperFundingSignature": keeper_funding_signature}
        if route_init_signatures:
            payload["routeInitSignatures"] = route_init_signatures
        if orchestrator_init_signature:
            payload["orchestratorInitSignature"] = orchestrator_init_signature
        if session_init_signatures:
            payload["sessionInitSignatures"] = session_init_signatures

        if self.dry_run:
            is_full = bool(route_init_signatures and orchestrator_init_signature and session_init_signatures)
            return {
                "dryRun": True,
                "id": transfer_id,
                "status": "processing" if is_full else "keeper_funded_awaiting_route_sigs",
                "confirmedSignatures": payload,
            }
        return self._request("POST", f"/transfers/{transfer_id}/confirm-broadcast", payload)

    def get_usage(self, from_date: str, to_date: str) -> dict[str, Any]:
        if self.dry_run:
            return {
                "dryRun": True,
                "fromDate": from_date,
                "toDate": to_date,
                "totalTransfers": 12,
                "completedTransfers": 12,
                "failedTransfers": 0,
                "totalVolumeRaw": "125000000000",
                "totalFlatFeeEarned": 504000,
                "totalPayouts": 12,
                "tokenFeesEarned": [
                    {"tokenMint": USDC_MINT, "tokenDecimals": 6, "amountRaw": "312500000"}
                ],
                "isTestMode": self.environment == "devnet",
            }
        q = urllib.parse.urlencode({"fromDate": from_date, "toDate": to_date})
        return self._request("GET", f"/usage?{q}")


def verify_webhook_signature(payload_bytes: bytes | str, signature_hex: str, secret: str) -> bool:
    """Verifies MultiHopper `x-multihopper-signature` header using HMAC-SHA256 and timing-safe comparison."""
    if not signature_hex or not secret:
        return False
    raw = payload_bytes.encode("utf-8") if isinstance(payload_bytes, str) else payload_bytes
    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_hex.strip().lower(), expected.lower())


def main():
    parser = argparse.ArgumentParser(description="MultiHopper AI Agent Treasury CLI")
    parser.add_argument("--env", choices=["devnet", "mainnet"], default="devnet")
    parser.add_argument("--wallet", default="St9fjc5egj6rufgBhFLoN5iDzzUgtD8jnTnwtdz5poQ")
    parser.add_argument("--demo", action="store_true", help="Run end-to-end dry-run agent treasury flow")
    args = parser.parse_args()

    client = MultiHopperClient(environment=args.env, dry_run=True)
    if args.demo:
        est = client.estimate_fees(USDC_MINT, 100_000_000, token_decimals=6, hops=5)
        tx = client.create_transfer(
            source_owner=args.wallet,
            token_mint=USDC_MINT,
            amount_raw=100_000_000,
            amount_tokens="100.0",
            token_decimals=6,
            token_symbol="USDC",
            destinations=[
                {"wallet": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "amountRaw": "60000000"},
                {"wallet": "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin", "amountRaw": "40000000"},
            ],
            hops=5,
        )
        prep = client.prepare_transfer(tx["id"])
        conf = client.confirm_broadcast(
            tx["id"],
            keeper_funding_signature="5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d",
            route_init_signatures=["4mGxFn7m8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"],
            orchestrator_init_signature="2qYzLf1x8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d",
            session_init_signatures=["7nKpQr3z8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"],
        )
        print(json.dumps({"estimate": est, "transfer": tx, "prepared": prep, "confirmed": conf}, indent=2))


if __name__ == "__main__":
    main()

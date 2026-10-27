# MultiHopper AI Agent Treasury & MCP Router (`multihopper-agent-treasury`)

- **Bounty:** [MultiHopper Build & Route Challenge | Up to $50,000 + Revenue Share](https://earn.superteam.fun/listings/bounty/multihopper-build-and-route-challenge-up-to-dollar50000-revenue-share-1)
- **Reward Pool:** $5,000 – $50,000 USDC + 0.25% Ongoing Integrator Revenue Share
- **Solana Payout & Integrator Wallet:** `St9fjc5egj6rufgBhFLoN5iDzzUgtD8jnTnwtdz5poQ`

## 1. Problem & Solution Overview
Autonomous AI agents operating treasuries, bounty boards, and multi-worker swarms on Solana need programmable, privacy-preserving payment rails with strict budget enforcement and compliance screening. Direct wallet-to-wallet transfers expose treasury topology and lack multi-hop scheduling or built-in TRM Labs screening.

**`multihopper-agent-treasury`** provides a zero-dependency Python SDK, CLI, and **Model Context Protocol (MCP) Server** (`mcp_server.py`) that integrates MultiHopper's v1 REST API (`https://devnet.multihopper.com/api/v1` and `https://multihopper.com/api/v1`) so any AI agent can:
1. Quote fees and integrator revenue share (`POST /api/v1/transfers/estimate`), accounting for the 50 bps standard tier (25 bps atomic integrator revenue share) and `0.002 SOL` per-wallet TRM compliance screening fee.
2. Execute single-recipient and **Multi-Send** (`destinations` up to 20 wallets) routed transfers (`POST /api/v1/transfers`) with UUIDv4 `Idempotency-Key` headers (`MH_070`/`MH_071` safe).
3. Enforce deterministic `AgentTreasuryPolicy` spend caps, mint allowlists (`SOL`, `USDC`, `USDG`), hop bounds (`3–10`), and `--dry-run` safety gates.
4. Orchestrate the two-stage transaction preparation and confirmation flow (`keeperFundingSignature` first, followed by `routeInitSignatures`, `orchestratorInitSignature`, and `sessionInitSignatures`).
5. Verify MultiHopper lifecycle webhooks (`transfer.quote_created`, `transfer.completed`, `payout.completed`, etc.) via constant-time HMAC-SHA256 (`x-multihopper-signature`).

"""Live execution via the Alpaca MCP server (README §6; satisfies the MCP/CLI rule).

`McpBroker` connects to an Alpaca MCP server with `langchain-mcp-adapters` (already in
requirements.txt) and submits each `OrderIntent`. Wrap it in `BrokerExecutor` and drop
it into the harness in place of the dry-run stub.

    from harness import BrokerExecutor
    from harness.mcp_executor import McpBroker
    executor = BrokerExecutor(McpBroker(SERVER_CONFIG))
    run_cycle(source, state, executor=executor)   # now trades for real

IMPORTANT — needs live verification. MCP option-order tool names and argument schemas
are SERVER-SPECIFIC, and this environment can't run the server, so:
  * set `order_tool` to your server's actual order tool name, and
  * pass a `build_args(intent) -> dict` that matches that tool's schema
    (whether it takes one multi-leg order or one call per leg is server-dependent).
The defaults are a best-effort starting point, not verified against a live server.
"""
from __future__ import annotations

from .orders import Broker, OrderIntent  # noqa: F401  (Broker documents the contract)


def _default_build_args(intent: OrderIntent) -> dict:
    """Best-effort mapping OrderIntent -> a generic order-tool payload. TUNE per server."""
    return {
        "symbol": intent.symbol,
        "structure": intent.structure,
        "quantity": intent.contracts,
        "expiry_days": intent.expiry_days,
        "net_side": intent.net_side,
        "legs": [l.to_dict() for l in intent.legs],
    }


class McpBroker:
    """Submits option orders through an Alpaca MCP server (best-effort; verify live)."""

    dry_run = False

    def __init__(
        self,
        server_config: dict,
        *,
        order_tool: str | None = None,
        build_args=_default_build_args,
    ):
        # server_config is the MultiServerMCPClient mapping, e.g.
        #   {"alpaca": {"command": "alpaca-mcp-server", "args": [...], "transport": "stdio"}}
        # or {"alpaca": {"url": "http://localhost:8000/sse", "transport": "sse"}}
        self.server_config = server_config
        self.order_tool = order_tool
        self.build_args = build_args

    async def _submit_async(self, intent: OrderIntent) -> dict:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(self.server_config)
        tools = await client.get_tools()
        by_name = {t.name: t for t in tools}

        name = self.order_tool
        if name is None:  # heuristic fallback — prefer verifying + setting order_tool
            candidates = [t.name for t in tools if "order" in t.name.lower()]
            if not candidates:
                raise RuntimeError(
                    "No order tool found on the MCP server; set McpBroker(order_tool=...). "
                    f"Available tools: {sorted(by_name)}"
                )
            name = candidates[0]

        if name not in by_name:
            raise RuntimeError(
                f"order_tool {name!r} not on the MCP server. Available: {sorted(by_name)}"
            )
        result = await by_name[name].ainvoke(self.build_args(intent))
        return {"status": "submitted", "tool": name, "result": result}

    def submit(self, intent: OrderIntent) -> dict:
        """Sync wrapper — fine for the one-shot cron cycle. In an async harness call
        `_submit_async` directly instead."""
        import asyncio

        return asyncio.run(self._submit_async(intent))


def make_mcp_executor(server_config: dict, **kwargs):
    """Convenience: a BrokerExecutor wired to a live Alpaca MCP server."""
    from .executor import BrokerExecutor

    return BrokerExecutor(McpBroker(server_config, **kwargs))

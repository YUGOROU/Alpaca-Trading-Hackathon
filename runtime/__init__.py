"""Runtime glue around the risk engine + harness.

Application-level modules that aren't the math engine (`risk_engine/`) or the agent
loop (`harness/`), but wire them into a runnable agent: the trade ledger, self-grading,
skill loading, and the strategy-context bridge.
"""

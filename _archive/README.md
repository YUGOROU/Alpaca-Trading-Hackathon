# _archive/ — retired, not deleted

Nothing here is dead-for-good — it's parked. When we retire a file or feature from the
active tree, it moves here (mirroring its original path) so it stays present and
restorable. To bring something back, `git mv` it from `_archive/<path>` to `<path>` and
re-wire its imports.

## What's here and why it was retired

| Path | Was | Why retired |
|---|---|---|
| `always-on/` | `loop.py`, `Dockerfile`, `docker-compose.yml`, `.dockerignore` | Always-on/Modal runtime — superseded by the GitHub Actions scheduled workflow. |
| `model/` | langchain OpenAI-compatible LLM client (`config.py`, `llm.py`) | The **DSH harness owns the model brain** now — no in-repo langchain. |
| `harness/mcp_executor.py` | langchain-mcp-adapters order executor | The **DSH harness owns the MCP connection** now. |
| `harness/llm.original.py` | full `harness/llm.py` incl. `make_llm_decider` | DSH approves all trades; the live `harness/llm.py` keeps only the `default_decider` stub. |
| `runtime/skills.py` | SKILL.md loader for the LLM decide prompt | Only fed the retired in-house LLM decider; DSH loads skills natively. |
| `webui/`, `scripts/run_webui.py` | our local web UI | DSH ships its own UI. |
| `tests/test_skills.py`, `tests/test_harness.original.py` | tests for the removed skills + LLM decider | Kept for reference alongside the code they covered. |
| `.github/workflows/agent.yml` | GitHub Actions 30-min cron running `scripts/run_agent.py` | Scheduling is now owned by the DSH-native heartbeat (`agent/dsh/heartbeat.js`); the cron would double-loop with it. |

Removed the corresponding langchain deps from `requirements.txt` and the model-provider
config from `.env.example`. See `docs/HARNESS_INTEGRATION.md` for the current division of
labor with the DSH harness.

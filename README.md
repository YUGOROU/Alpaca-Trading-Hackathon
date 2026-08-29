# Liquidity Leak — an AI agent that protects a portfolio like a risk manager

<sub>Team **Liquidity Leak** · Alpaca × lablab.ai hackathon</sub>

> An autonomous agent that holds a real book of stocks and **defends it with options**.
> It measures risk with hard math, decides posture with a reasoning model, steps a hedge
> in and out as conditions change, and writes down why — then grades itself.

It never guesses which way the market will go. It makes sure a bad day can't hurt us, and
quietly collects premium while it waits.

---

## The idea

Most "AI trading" bots predict direction and bet on it — a coin flip in a nice costume.
And naive hedging *loses* money: protection costs premium, so in a calm week an insurance
bot just bleeds. **We solve both.**

We hold an appreciating **core book** (liquid ETFs + mega-caps) and treat options as a
risk overlay, not a wager:

1. **The book's normal returns** carry the P&L — stocks go up over time.
2. **Financed protection.** Collars and defined-risk spreads pay for most of the hedge, so
   protection runs near-zero drag.
3. **Selling overpriced premium** — options are, on average, priced richer than reality
   delivers. We harvest that gap as a net premium seller, with every loss capped by design.

The result is the profile we want: steady, mostly green, no blow-ups, and a hedge that
*visibly* saves the book in a sell-off. We win on **risk-adjusted metrics** — drawdown,
volatility, Sharpe — not on a lucky directional call.

---

## How it works

Every cycle the agent runs one loop:

```
OBSERVE  read the live book + market            (Alpaca, read-only)
MEASURE  run the risk engine                    ← deterministic Python
DECIDE   model sets the posture, with reasons   ← the model's real job
EXECUTE  place / adjust the options overlay     (paper, fail-closed)
LOG      write what it did and why → self-grade
```

The split is the whole point:

| Deterministic code owns (the math) | The model owns (the judgment) |
|---|---|
| Exposure, volatility, VaR, stress tests | *When* risk is worth hedging right now |
| How many contracts for a given coverage | *How much* to protect, given cost + regime |
| Whether protection is cheap today | *When* to release a hedge that's no longer needed |
| All order sizing and hard risk caps | Reading soft context the numbers miss, and explaining the move |

**The model never does arithmetic and never invents an order.** It picks one of a set of
pre-vetted, pre-sized candidates. Numbers measure; the model judges; the log remembers.

---

## What it measures

A compact risk snapshot each cycle, rolled into a single **Risk Score (0–100)** that drives
the hedge:

- **Beta-weighted exposure** — the book's true market-equivalent dollars at risk
- **EWMA volatility + VaR** — the live "normal bad day" loss, sharpened for fat tails
- **Drawdown from peak** and **regime** (index vs its 50-day trend) — the stress signals
- **IV Rank + variance risk premium** — is protection cheap, and is premium worth selling
- **Coverage & hedge-cost drag** — how much is protected, and what it costs

`Risk Score → target coverage.` Calm ⇒ near-zero hedge (no drag). Stress ⇒ step protection
up. The score computes automatically; the model decides whether it justifies acting now.

---

## Architecture

- **Risk engine** (`risk_engine/`) — pure-Python, dependency-free options + portfolio math.
  Runs offline, fully tested. The deterministic core everything else reads.
- **Harness: DSH.** The agent brain runs on the DSH harness — MCP-native, model-agnostic.
  It sees exactly two tools: read the account, and submit one candidate. Order sizing and
  risk caps are hidden from the model behind a deterministic gate.
- **Runtime: a custom market-monitoring heartbeat** (`agent/dsh/heartbeat.js`) — a
  DSH-native loop that wakes on an interval during market hours, runs one decide cycle, and
  can place paper orders autonomously. No external cron, no infra to stand up.
- **Models: open models from Hugging Face** Inference Providers, selected behind one config
  seam so we can swap reasoning brains freely — with **domain finetuning on finance /
  tool-use** as the edge we're building next.
- **Execution is paper-enforced and fail-closed** — it resolves each hedge to a real listed
  contract and refuses anything it can't size safely, never guessing an order.

Every downside is capped by hard limits the model cannot override: per-trade premium,
per-underlying risk, total option risk, and a daily-loss halt.

---

## Run it

```bash
# 1. Seed the core book on the Alpaca paper account (dry-run first; --execute to place)
python scripts/seed_book.py            # show the plan
python scripts/seed_book.py --execute  # place the paper buy orders

# 2. Take one live decision cycle — read the book + market, size the posture
python -m agent.cli context --live
```

Run the always-on monitoring heartbeat through DSH (interval-driven, market-hours gated,
autonomous paper placement):

```bash
dsh --profile portfolio-agent --live --heartbeat --place "protect the book"
```

Credentials come from `.env` (`ALPACA_API_KEY` / `ALPACA_SECRET_KEY`, paper). The whole
stack runs offline against a mock feed when no keys are set — no account needed to develop.

---

## Proving it works

Five live days is noise, so we also **replay the engine through real historical crashes**
(`backtest/`) — COVID 2020, the 2022 bear, the 2024 vol spike — and show two equity curves:
**unhedged vs. hedged.** The hedge visibly cushions the drop. Paired with the live
**adaptive hedge timeline** (the agent stepping in as risk rose, releasing as it fell, with
its logged reasoning) and the **self-grading log** (did the protection earn its cost?), the
story is defense you can watch, not a backtest you have to trust.

---

## Status

Live read path verified against a real Alpaca paper account; the risk engine, candidate
gate, heartbeat, and paper-placement path are wired end-to-end. Next: fill the seeded book,
confirm the hedge stepping in on live stress, and finetune the reasoning model on the domain.

<div align="center">

# 🛡️ Liquidity Leak

### An autonomous AI agent that holds a real book of stocks and **defends it with options** — like a risk manager, not a gambler.

<sub>Team **Liquidity Leak** · Alpaca × lablab.ai Hackathon · *Options Alpha Agents*</sub>

![tests](https://img.shields.io/badge/tests-118%20passing-2ea44f?style=for-the-badge)
![python](https://img.shields.io/badge/python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
![node](https://img.shields.io/badge/runtime-DSH%20%C2%B7%20Node%2022-339933?style=for-the-badge&logo=node.js&logoColor=white)
![deploy](https://img.shields.io/badge/deploy-Modal%20always--on-7b3fe4?style=for-the-badge)
![mode](https://img.shields.io/badge/execution-paper%20%C2%B7%20fail--closed-orange?style=for-the-badge)
![license](https://img.shields.io/badge/license-MIT-blue?style=for-the-badge)

</div>

---

> **It never guesses which way the market will go.** It measures risk with hard math,
> decides posture with a reasoning model, steps a hedge in and out as conditions change,
> writes down *why* — then grades itself. A bad day can't hurt the book, and it quietly
> collects premium while it waits.

---

## 📖 Table of contents

- [The idea](#-the-idea)
- [System architecture](#-system-architecture)
- [The decision loop](#-the-decision-loop)
- [Per-cycle protocol](#-per-cycle-protocol-model--deterministic-core)
- [What it measures](#-what-it-measures)
- [Strategy postures](#-strategy-postures)
- [How the hedge reshapes P&L](#-how-the-hedge-reshapes-pl)
- [The core book](#-the-core-book)
- [Hard risk caps](#-hard-risk-caps-the-model-cannot-override-these)
- [Model selection & qualification](#-model-selection--qualification)
- [Deployment](#-deployment-always-on-heartbeat)
- [Repository map](#-repository-map)
- [Run it](#-run-it)
- [Testing](#-testing)
- [Roadmap](#-roadmap)

---

## 💡 The idea

Most "AI trading" bots predict direction and bet on it — a coin flip in a nice costume.
And naïve hedging *loses* money: protection costs premium, so in a calm week an insurance
bot just bleeds. **We solve both** by treating options as a risk overlay, not a wager, on
top of an appreciating core book of liquid ETFs + mega-caps.

Three engines drive the P&L, each with its downside capped by design:

```mermaid
flowchart LR
    subgraph P["Three profit engines · each loss-capped"]
        direction TB
        E1["📈 <b>Core book drift</b><br/>diversified ETFs + mega-caps<br/>carry the baseline return"]
        E2["🪙 <b>Sell overpriced premium</b><br/>options price richer than reality<br/>delivers — harvest the gap,<br/>defined-risk both sides"]
        E3["🛡️ <b>Financed protection</b><br/>collars + spreads pay for the<br/>hedge → near-zero drag"]
    end
    E1 --> R["🎯 <b>The profile we want</b><br/>steady · mostly green · no blow-ups<br/><i>won on risk-adjusted metrics:<br/>drawdown · volatility · Sharpe</i>"]
    E2 --> R
    E3 --> R
    style R fill:#0d3b2e,stroke:#2ea44f,stroke-width:2px,color:#fff
```

---

## 🏗️ System architecture

A clean split: **deterministic Python owns the math**, the **model owns the judgment**, and
a **fail-closed executor** owns the orders. Nothing crosses those lines.

```mermaid
flowchart LR
    subgraph DATA["📡 Data · read-only"]
        A1["Alpaca paper feed"]
        A2["Mock feed / fixtures<br/><i>runs fully offline</i>"]
    end

    subgraph CORE["🧮 Deterministic core · risk_engine/"]
        direction TB
        C1["<b>metrics</b><br/>vol · VaR · tails · beta"]
        C2["<b>blackscholes</b><br/>price · Δ Θ Γ vega"]
        C3["<b>scoring</b><br/>Risk Score → coverage"]
        C4["<b>engine + income</b><br/>overlay sizing"]
        C5["<b>limits</b><br/>hard risk caps"]
    end

    subgraph BRAIN["🧠 Harness + model · agent/dsh/"]
        direction TB
        B1["DSH heartbeat<br/><i>interval-driven</i>"]
        B2["model-native adapter<br/><i>2 forced tool calls</i>"]
        B3["decision bridge"]
        B4["🤖 Reasoning model<br/><b>GLM-5.3</b> via HF"]
    end

    subgraph EXEC["⚙️ Execution · paper, fail-closed"]
        E1["candidate gate"]
        E2["Alpaca orders<br/><i>resolves real contracts</i>"]
    end

    subgraph OBS["📒 Observability"]
        O1["decision ledger"]
        O2["self-grading"]
        O3["backtest replay"]
    end

    DATA --> CORE
    B1 --> B2
    B2 <--> B4
    B2 --> B3 --> CORE
    CORE --> E1 --> E2
    E2 --> OBS
    CORE --> O1
    O1 --> O2

    style CORE fill:#0b2545,stroke:#4d8bf0,color:#fff
    style BRAIN fill:#3a1c5e,stroke:#a06be0,color:#fff
    style EXEC fill:#5e3a1c,stroke:#e0a06b,color:#fff
```

**The model never does arithmetic and never invents an order.** It picks exactly one of a
set of pre-vetted, pre-sized candidates. Numbers measure; the model judges; the log remembers.

---

## 🔄 The decision loop

Every cycle the agent runs one loop — and the split between the two owners is the whole point.

```mermaid
flowchart LR
    O["👁️ <b>OBSERVE</b><br/>live book + market<br/><sub>Alpaca, read-only</sub>"]
    M["🧮 <b>MEASURE</b><br/>risk engine<br/><sub>deterministic Python</sub>"]
    D["🧠 <b>DECIDE</b><br/>model sets posture<br/><sub>the model's real job</sub>"]
    V["🚦 <b>VALIDATE</b><br/>hard risk caps<br/><sub>model cannot override</sub>"]
    X["⚙️ <b>EXECUTE</b><br/>adjust the overlay<br/><sub>paper, fail-closed</sub>"]
    L["📝 <b>LOG + GRADE</b><br/>what it did & why"]
    O --> M --> D --> V --> X --> L
    L -.->|next cycle| O
    style D fill:#3a1c5e,stroke:#a06be0,color:#fff
    style M fill:#0b2545,stroke:#4d8bf0,color:#fff
```

| Deterministic code owns — **the math** | The model owns — **the judgment** |
|---|---|
| Exposure, volatility, VaR, stress tests | *When* risk is worth hedging **right now** |
| How many contracts for a given coverage | *How much* to protect, given cost + regime |
| Whether protection is cheap today | *When* to release a hedge no longer needed |
| All order sizing and hard risk caps | Reading soft context the numbers miss, and explaining the move |

---

## 🔌 Per-cycle protocol (model ↔ deterministic core)

The harness exposes the model **exactly two tools** and forces their order. Order sizing,
strikes, and risk caps are computed deterministically and hidden behind the gate — the model
only ever *chooses*.

```mermaid
sequenceDiagram
    autonumber
    participant HB as ⏰ Heartbeat
    participant A as 🔀 Model-native adapter
    participant M as 🤖 Reasoning model
    participant B as 🌉 Decision bridge
    participant E as 🧮 Risk engine
    participant G as 🚦 Candidate gate
    participant X as ⚙️ Executor
    participant L as 📒 Ledger

    HB->>A: wake (interval · market hours)
    A->>M: force get_decision_context
    M-->>A: tool call (temperature 0)
    A->>B: get_decision_context
    B->>E: assess() + plan_strategy()
    E->>G: validate_plan() — hard caps
    G-->>A: risk snapshot + admissible candidates
    A->>M: force submit_decision(candidate)
    M-->>A: pick ONE candidate + reason
    A->>B: submit_decision (harness-owned id)
    B->>X: place approved orders (only if armed)
    X-->>L: fills / intents
    B->>L: decision + reason → self-grade
```

---

## 📊 What it measures

A compact risk snapshot every cycle — dozens of institutional-grade measures, all pure
functions, all unit-tested, all dependency-free:

```mermaid
mindmap
  root(("🧮 Risk<br/>Engine"))
    Exposure
      Beta-weighted delta
      OLS beta
      Downside beta
      Min-variance hedge ratio
    Volatility & VaR
      EWMA vol · λ=0.94
      Parametric VaR 95/99
      Historical VaR
    Tail risk
      Expected Shortfall · CVaR
      Cornish–Fisher VaR
      Skew & excess kurtosis
    Premium signals
      IV Rank
      Variance Risk Premium
      Put skew
    Pricing · Black–Scholes
      Price · Δ · Θ · Γ · Vega
      Strike-for-delta
    Liquidity gates
      Relative spread
      Two-sided quote check
```

Those signals roll into a single **Risk Score (0–100)** that drives the hedge. The mapping is
an exact, transparent function — **calm ⇒ near-zero hedge (no drag); stress ⇒ step protection up:**

```mermaid
xychart-beta
    title "Risk Score → target hedge coverage"
    x-axis "Risk Score (0–100)" [0, 25, 50, 75, 100]
    y-axis "Target coverage (%)" 0 --> 100
    line [0, 0, 50, 100, 100]
```

And the **inverse dial** governs premium selling — harvest hardest when options are richest
versus realized volatility (positive variance risk premium), damped to zero as the regime turns:

```mermaid
xychart-beta
    title "Variance risk premium → income aggressiveness (calm regime)"
    x-axis "VRP (vol points)" [0, 2, 4, 6, 8, 10]
    y-axis "Aggressiveness (%)" 0 --> 100
    line [0, 25, 50, 75, 100, 100]
```

---

## 🎛️ Strategy postures

The two dials combine into one of four stances each cycle. The agent moves between them
automatically as regime and premium richness change:

```mermaid
stateDiagram-v2
    [*] --> SIT
    SIT: 😌 SIT<br/>cheap IV · calm<br/>nothing to sell, nothing to fear
    HARVEST: 🪙 HARVEST<br/>rich IV · calm<br/>sell premium, minimal hedge
    HARVEST_HEDGE: ⚖️ HARVEST + HEDGE<br/>rich IV · rising risk
    DEFEND: 🛡️ DEFEND<br/>risk-off<br/>hedge steps in, income stands down

    SIT --> HARVEST: VRP turns rich
    HARVEST --> HARVEST_HEDGE: risk rising
    HARVEST --> DEFEND: regime risk-off
    SIT --> DEFEND: regime risk-off
    HARVEST_HEDGE --> DEFEND: stress deepens
    DEFEND --> HARVEST: risk clears
    HARVEST_HEDGE --> HARVEST: risk clears
    DEFEND --> SIT: calm & cheap IV
```

---

## 🛡️ How the hedge reshapes P&L

The point of the overlay: **cap the left tail while keeping the upside.** Below is the payoff
mechanism of the protective overlay — book alone vs. book + hedge across an adverse move.
*(Illustrative of the protective-put mechanism, not a historical return series.)*

```mermaid
xychart-beta
    title "Book P&L vs. underlying move — unhedged (bleeds) vs. hedged (floored)"
    x-axis "Underlying move (%)" [-15, -10, -5, 0, 5, 10]
    y-axis "Book P&L ($k)" -16 --> 12
    line [-15, -10, -5, 0, 5, 10]
    line [-6, -6, -5, -1, 4, 9]
```

> The lower line is the naked book — a straight loss into a sell-off. The upper line is
> book + hedge: the left tail flattens (the put pays), the small gap at 0% is the financed
> premium, and the upside is preserved. **Defense you can watch, not a backtest you must trust.**

We also **replay the engine through real historical crashes** (`backtest/`) — showing the
unhedged vs. hedged equity curves so the cushion is visible over more than five live days.

---

## 🧺 The core book

Fixed **upfront** — the agent *protects* the book, it never picks it. Diversified across
market, size, and sector so no single name dominates the P&L, yet every holding stays highly
correlated to SPY so the **SPY put hedge actually covers the book** (low basis risk). ~80%
invested; the rest is a cash sleeve for premium + margin.

```mermaid
pie showData
    title Core book — target weights (% of equity)
    "SPY" : 24
    "Cash" : 20
    "QQQ" : 11
    "IWM" : 10
    "DIA" : 9
    "XLV" : 8
    "XLF" : 7
    "XLE" : 4
    "NVDA" : 3
    "AAPL" : 2
    "MSFT" : 2
```

Weights (not hard-coded share counts) are the single source of truth, so the same definition
sizes correctly at any account value or price — see [`risk_engine/book.py`](risk_engine/book.py).

---

## 🚦 Hard risk caps (the model cannot override these)

Every downside is bounded by a deterministic gate that runs *after* the model decides.
Offending income is **scaled down, never silently dropped**, so the model only ever chooses
from a pre-validated, safe set — see [`risk_engine/limits.py`](risk_engine/limits.py).

| Guardrail | Cap | Behaviour when breached |
|---|---|---|
| 🩸 **Daily-loss halt** | −5% on the day | Stop opening new premium (keep the hedge on) |
| 📦 **Total option risk** | ≤ 30% of equity | Scale income down to fit |
| 🎯 **Per-underlying risk** | ≤ 15% of equity | Scale the offending symbol down |
| 🛡️ **Hedge premium / cycle** | ≤ 5% of equity | Flag (never auto-reduce protection) |

---

## 🤖 Model selection & qualification

The reasoning brain sits behind a single config seam so we can swap open models freely. We
don't take a vendor's word for it — every candidate runs a **30-call transport qualification**
(10 direct + 20 through the DSH harness) with a strict, forced two-tool schema. A candidate
that emits malformed tool calls, drifts schema, or times out is **disqualified**.

```mermaid
xychart-beta
    title "Transport qualification — successful tool calls (of 30)"
    x-axis ["GLM-5.3", "DSV4-Pro", "DSV4-Flash"]
    y-axis "Calls passed" 0 --> 30
    bar [30, 18, 25]
```

| Candidate | Verdict | Direct (10) | DSH (20) | Schema-invalid | Timeouts |
|---|---|---|---|---|---|
| **GLM-5.3** | ✅ **Passed** | 10/10 | 20/20 | 0 | 0 |
| DSV4-Pro | ❌ Failed | 0/10 | 18/20 | 12 | 0 |
| DSV4-Flash | ❌ Failed | 9/10 | 16/20 | 5 | 0 |

Only **GLM-5.3** is transport-stable across all 30 calls; both DeepSeek candidates fail on
native schema instability (not parse/timeout artifacts). Evidence is committed under
[`results/`](results/). The model-native adapter normalizes each provider's dialect (standard
tool-calls, GLM XML, DeepSeek DSML) back to one canonical call — no prompt demonstrations,
temperature 0, harness-owned decision ids.

---

## ☁️ Deployment: always-on heartbeat

Packaged as a durable **Modal** app — an always-on CPU container that owns the heartbeat and
exposes an HTTP liveness probe. The deployed runtime mounts a dedicated **paper-only** Alpaca
Secret and can execute one gate-approved, opening SPY overlay per eligible cycle: a protective
put, covered call, or iron condor. It cannot trade the Core Book, use a live account, choose
arbitrary symbols or sizes, submit batches, or close option structures autonomously. A later
close/roll slice requires contract-level ledger provenance so it cannot confuse a hedge with an
income-spread leg.

Each accepted autonomous protective-put open is recorded in an append-only contract-provenance
sidecar with its exact OCC contract, quantity, broker order id, strategy, and leg role. This is
the source of intent for a future close/roll gate; the broker remains the source of truth.

```mermaid
flowchart TB
    GH["🚀 GitHub Action<br/>modal deploy"] --> APP
    SEC["🔑 huggingface Secret<br/><i>HF_TOKEN only</i>"] --> APP
    CFG["⚙️ HF_MODEL_ID env<br/><i>GLM-5.3 · non-secret config</i>"] --> APP
    APP["📦 Modal App · always-on CPU container"] --> HS["❤️ heartbeat_server<br/>:8080 /healthz"]
    HS --> LOOP["🔁 DSH heartbeat loop<br/>every 30 min"]
    LOOP --> DEC["🧠 one decide cycle"]
    DEC --> VOL[("💾 state volume<br/>decisions.jsonl")]
    SEC2["🔑 alpaca-paper Secret<br/>paper credentials only"] --> APP
    G["🚦 deterministic gate + fresh revalidation"] --> DEC
    style APP fill:#2a1a4a,stroke:#a06be0,color:#fff
```

---

## 🗂️ Repository map

```
risk_engine/     Pure-Python options + portfolio math — the deterministic core
  ├─ metrics.py       vol · VaR · tails · beta · liquidity (all pure functions)
  ├─ blackscholes.py  price · delta · theta · gamma · vega · strike-for-delta
  ├─ scoring.py       Risk Score (0–100) → target coverage · income aggressiveness
  ├─ engine.py        assess() snapshot + plan_hedge / plan_strategy
  ├─ income.py        covered calls + iron condors (theta harvest)
  ├─ limits.py        hard risk caps (the gate)
  └─ book.py          fixed target-weight core book + rebalance math
agent/           The reasoning layer
  ├─ candidates.py    builds the pre-vetted candidate set
  ├─ gate.py          deterministic admissibility gate
  ├─ cli.py           context / submit entrypoints (the deterministic bridge)
  ├─ model_evaluation.py  candidate qualification protocol
  └─ dsh/             DSH harness · model-native adapter · heartbeat · paper executor (Node)
feed/            Data sources — Alpaca (live) + mock (offline), one interface
harness/         Order translation + paper executor (fail-closed)
runtime/         Self-grading + the strategy API
backtest/        Historical crash replay — hedged vs. unhedged equity curves
deploy/          Modal always-on heartbeat + model-eval apps
results/         Committed model-qualification evidence
tests/           93 Python tests · agent/dsh/tests 25 Node tests
```

---

## ▶️ Run it

```bash
# 1. Seed the core book on the Alpaca paper account (dry-run first)
python scripts/seed_book.py            # show the plan
python scripts/seed_book.py --execute  # place the paper buy orders

# 2. Take one live decision cycle — read the book + market, size the posture
python -m agent.cli context --live
```

Run the always-on monitoring heartbeat through DSH (interval-driven, market-hours gated):

```bash
dsh --profile portfolio-agent --live --heartbeat "protect the book"
```

Credentials come from `.env` (`ALPACA_API_KEY` / `ALPACA_SECRET_KEY`, paper). **The whole
stack runs offline against a mock feed when no keys are set** — no account needed to develop.

---

## ✅ Testing

```bash
python -m unittest discover -s tests        # 74 Python tests
cd agent/dsh && npm test                    # 20 DSH / adapter tests
```

**118 tests, zero external services required.** The deterministic core is exercised end-to-end
— metrics, pricing, scoring, sizing, caps, the candidate gate, the model-native adapter, and
the paper-placement path — all offline.

---

## 🗺️ Roadmap

```mermaid
timeline
    title From engine → autonomous defender
    Shipped : Pure-Python risk engine (94 tests) : DSH model-native adapter : GLM-5.3 transport qualification : Modal always-on heartbeat
    Now : Autonomous bounded SPY paper overlays : Seed the book + watch the hedge step in and out on stress
    Next : Domain-finetune the reasoning model on finance / tool-use : Multi-underlying, cross-hedged books
```

---

<div align="center">

**Team Liquidity Leak** · built for the Alpaca × lablab.ai Hackathon
Numbers measure. The model judges. The log remembers.

<sub>MIT licensed · paper-trading only · not investment advice</sub>

</div>

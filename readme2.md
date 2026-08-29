# Alpaca AI Trading Agent — Track 3: Hedging & Risk Protection

> Master plan for our lablab.ai × Alpaca hackathon entry. We **pivoted from Track 1
> (Options Alpha) to Track 3 (Hedging & Risk Protection)** — this doc is the single
> source of truth: the strategy, how we actually make money, the model picks, the
> harness question (OpenClaw / Hermes vs. our own loop), the runtime, and — most
> importantly — the **full math engine** the agent runs on.
>
> Written in plain language on purpose. The only heavy part is §7 (the math), and
> that's meant to be heavy — it's the core of the whole thing.

---

## ⚠️ Read first — the Track 3 P&L trap

A hedge is **insurance.** In a calm or rising market it *costs* money (premium drag)
and only pays off in a sell‑off. Over the **~5–6 day judged window**, if no crash
happens, a naive hedging agent shows a **small loss** — worse for the P&L criterion
than a directional bet. **This is the central design problem of Track 3,** and the
whole plan is built to beat it:

- Hold an **appreciating base book** (net long) so a normal week is green; the hedge
  is a small overlay, not the main position.
- Use **collars** (near‑zero cost), not naked puts, to kill the drag.
- Be **adaptive** — only pay for protection when stress is actually detected; sit
  unhedged (zero drag) when calm.
- **Prove the value with a historical‑crash backtest** in the video (§8), where the
  hedge visibly saves the book.
- **Compete on risk‑adjusted metrics** (max drawdown, volatility, Sharpe) — a hedger
  wins on drawdown control even when raw P&L is modest.

> Eyes open: if the team wants *raw* 5‑day P&L, Track 1 is the safer bet. Track 3
> wins on **originality + presentation + defense narrative**, with P&L defended by
> the tactics above and in §3.

---

## 1. Why we switched to Track 3

The original plan was **Track 1 (Options Alpha)** — the agent predicts which way a
stock will go and buys a call or put. The problem Yugo kept pointing at (and he's
right): predicting short‑term direction with technical indicators + an LLM is, in
practice, **a coin flip dressed up in a nice story.** Worse, *buying* options is a
losing game on average — you pay for "insurance" that's usually overpriced, and
time decay bleeds you daily. No real edge.

**Track 3 — Hedging & Risk Protection — fixes this.** The official task:

> *"Build agents that protect portfolios using options overlays and adaptive risk
> gates. This track rewards defense: agents that know **when** to step in, **how
> much** protection to buy, and **when** to release a hedge that's no longer
> needed."*

Why this is a much better fit for us:

- **It stops the LLM from guessing prices.** The agent no longer bets on direction.
  It watches a portfolio, measures risk with hard math, and decides how much to
  protect. Math does the numbers; the LLM only makes the judgment calls.
- **It plays to an agent's real strengths** — a long‑running loop that observes,
  plans, uses tools, and reacts, instead of a one‑shot fortune‑teller.
- **It leans on what we're already best at** — the risk‑gate layer from the old
  plan becomes the *whole product*, not a side feature.
- **It can't be dismissed as a coin flip**, which is exactly the criticism we
  needed to answer before locking a track.

---

## 2. What the agent actually does (in one paragraph)

We hold a normal **basket of stocks/ETFs** (the "core book"). The agent's job is to
**protect that book with options** and adjust the protection as conditions change.
Every cycle it asks: *How much market risk am I carrying right now? Is that risk
rising or falling? Do I have enough protection? Is protection cheap or expensive
today? Should I add a hedge, hold, or take one off?* It answers with options
overlays — mostly **collars** and **put spreads** — sized by math, chosen by the
LLM, and logged so it can grade itself later.

---

## 3. How we make money (the honest version)

A pure hedger **loses** money slowly — protection costs premium. So profit can't
come from "buying insurance." It comes from three places:

1. **The core book's normal returns.** We own stocks/ETFs; they go up over time.
   The hedge just stops a bad week from wrecking us. In a *down* week, the hedge
   pays off and we lose far less than the market — that's the hero story for judges.
2. **Financing the hedge with collars.** A **collar** = own the stock + buy a put
   (protection, costs money) + sell a call (income). The call income *pays for*
   the put, often bringing the cost near **zero or a small credit.** So we get
   protection with almost no drag. Collars are a listed Track‑3 strategy, so this
   keeps us squarely in‑track.
3. **Selling overpriced premium (the real edge).** Options are, on average,
   *overpriced* (implied volatility usually ends up higher than what actually
   happens — the "variance risk premium"). By being a **net seller** of premium
   (the short‑call leg of collars, or defined‑risk put spreads) we harvest that
   overpricing. This is a documented, repeatable edge — not a prediction.

**Profit = core returns + collected premium − protection cost − any capped losses.**

Realistic size on a $100k paper account over ~5 trading days: small — a fraction of
a percent to maybe ~1–2%. **That's fine.** P&L is only 1 of 5 judging criteria, and
Track 3 is judged on *defense quality*. The winning profile here is **steady, mostly
green, no blow‑ups, with a clear risk story** — which short‑premium + hedging
naturally produces (high win‑rate, small wins, and losses that are *capped* by
design).

---

## 4. The agent loop

```
        ┌─────────────────────────────────────────────┐
        │  every cycle (heartbeat / event trigger):    │
        │                                              │
        │  1. OBSERVE   read the book + market (MCP)    │
        │  2. MEASURE   run the math engine (§7)        │  ← deterministic code
        │  3. DECIDE    LLM sets the risk posture       │  ← the LLM's real job
        │  4. EXECUTE   place/adjust options via MCP    │
        │  5. MONITOR   check hedges, stops, expiries   │
        │  6. LOG       write what it did + why         │  ← self‑grading trail
        └─────────────────────────────────────────────┘
```

**The split that matters:**

| Deterministic code owns (the math) | The LLM owns (the judgment) |
|---|---|
| Greeks, beta‑weighted delta, VaR, stress tests | *When* to step in (is this risk worth hedging now?) |
| How many contracts to buy for X% coverage | *How much* protection (full vs. partial, given cost + regime) |
| Whether protection is cheap (IV Rank) | *Which* structure (collar vs. put spread vs. roll) |
| Hedge cost, coverage ratio, roll timing | *When* to release a hedge that's no longer needed |
| All order sizing + risk limits | Reading news/events the numbers don't show yet, + writing the reasoning |

This division is the answer to *"why is this an AI agent and not just a script?"* A
script uses fixed thresholds; our agent adapts its posture over **both** the
numbers **and** soft context (a Fed meeting tomorrow, a scary headline), and it
explains every move.

---

## 5. Models (SOTA picks)

We use **paid Hugging Face Inference Providers**, funded by Yugo's ~$100 in
hackathon winnings. Two lanes:

- **Reasoning brain → Kimi K3.** Strong agentic reasoning and finance knowledge;
  it reportedly beats Claude Fable 5 on **τ³‑Banking** (a finance tool‑use
  benchmark), which is exactly our domain. This is the model that reads the math
  and makes the posture call.
- **Fast lane → Gemma 4 31B on Cerebras.** For cheap, high‑frequency steps
  (headline sentiment, quick checks). Cerebras is extremely fast — Yugo measured
  **~780 tok/s** vs. ~54 tok/s for Opus 4.8 on Artificial Analysis. Caveat: the
  free tier is now a one‑time **$5 trial** with an **8k‑token context cap**, so
  it's a *fast‑lane* model, not the reasoning brain.
- **Fallbacks:** Gemini (free tier) and Groq (Llama, free) stay available.

Everything routes through one config seam (`agent/config.py` + `agent/llm.py`):
the model for each role lives in `.env`, so we swap Kimi ↔ Gemma ↔ Gemini by
editing one file — no code changes. Each teammate can run a different model for the
bake‑off.

---

## 6. Harness & runtime

### Harness — OpenClaw / Hermes vs. our own loop

The **Alpaca MCP server + CLI are the *tools* ("hands")** — they place orders and
read the account. They are **not** the agent loop. We still need a "brain loop" to
drive them. Three options:

- **Hermes** (Nous Research) — a model‑agnostic agent harness that **natively
  speaks MCP**, and after each task **writes down what it tried, what worked, and
  what failed.** That last part is basically our self‑grading loop for free, and
  MCP‑native removes our integration worry.
- **OpenClaw** — an autonomous agent framework whose signature **"heartbeat"**
  (a scheduled self‑wake to monitor things) maps almost exactly onto our
  observe‑and‑rebalance loop.
- **Our own lean loop** — ~a couple hundred lines of Python: a simple cycle that
  calls the model + MCP tools and checkpoints state. Modern, model‑driven, zero
  integration risk, fully under our control.

**Decision rule:** both Hermes and OpenClaw are powerful but **new (months old)**
and carry a lot of machinery we won't use for a bounded hedging loop.
- If **Yugo already runs Hermes/OpenClaw and owns the integration** → use it
  (Hermes is the better fit: MCP‑native + its memory = our self‑grading loop).
- If we'd have to **stand it up cold under a 1‑week deadline** → use our **own lean
  loop** and rebuild the (simple) heartbeat + log ourselves.

Either way, our model‑selection seam is harness‑agnostic, so we can start on the
lean loop today and swap later. **The harness doesn't win the hackathon — the
strategy and the demo do — so we won't sink the week into it.**

### Runtime

**Decision: GitHub Actions is the runtime.** A scheduled workflow
([.github/workflows/agent.yml](.github/workflows/agent.yml)) runs one agent cycle every
30 min during US market hours (`*/30 13-21 * * 1-5` UTC) and commits the updated
`state/state.json` + `state/ledger.jsonl` back to the repo, so state persists across runs
with **zero infrastructure to stand up or pay for** — the right call under a one‑week
deadline. `scripts/run_agent.py` is the single entry point (used by the workflow and for
local one‑shot runs); `git` is the state store.

The tradeoff we're accepting: a 30‑min heartbeat has gaps, so this is periodic
monitoring, not the *continuous* stream a mature hedging agent eventually wants
(stop‑losses, hedge drift, expiries reacting the moment something happens). An
**always‑on, event‑driven runtime** (e.g. Modal CPU subscribing to Alpaca's live
fill/quote stream) is the natural next step **post‑hackathon** — but it buys continuity
we don't need to demo the thesis, at a cost in setup we can't spare now. State is already
checkpointed every cycle, so swapping the scheduler later is a runtime change, not a
rewrite.

---

## 7. The math engine (the heavy part)

This is the deterministic core. **The LLM never does arithmetic** — Python computes
all of this from Alpaca data, and the LLM reads the results to make its call.
Everything below is standard, well‑understood options math. Worked examples use
round numbers.

> **Standing assumptions:** 252 trading days/year; "annual volatility" means the
> annualized standard deviation of returns; each option contract = **100 shares**;
> we ignore tiny effects (rho / interest rates) at this scale.

### 7.0 The must-build essentials — the ~40–50% that carries the demo

**Build only this and the agent still runs end-to-end and demos well:** a base book
that gets protected, a Risk Score that visibly steps the hedge in and out, and a
decision log. Everything else in the full catalog (§7.0.1) is **credibility + roadmap**
— described in the pitch, built only if time allows. This is the MVP subset of Core.

| # | Must-build | Why it's essential | § |
|---|---|---|---|
| 1 | **Delta** (Greek) | The exposure we hedge; share-equiv = delta × 100 | 7.1 |
| 2 | **Beta-weighted delta** | The book's true SPY-equivalent exposure (sizing basis) | 7.2 |
| 3 | **EWMA realized volatility** | The live vol number that feeds risk | 7.4 |
| 4 | **Drawdown from peak** | Core stress signal — how far off the high | 7.12 |
| 5 | **Regime — SPY vs 50-day MA** | Risk-on / risk-off signal | 7.12 |
| 6 | **Basic VaR** | Headline "normal bad day" loss | 7.4 |
| 7 | **Risk Score (0–100)** | Composite of the signals — the dial that drives everything | 7.12 |
| 8 | **Step-in / release thresholds** | Risk Score → target coverage %; the adaptive logic | 7.12 |
| 9 | **Hedge ratio (delta-match)** | How many puts for the target coverage | 7.3 |
| 10 | **Coverage ratio** | How much of the book is protected right now | 7.10 |
| 11 | **Expected move (from IV)** | Sets the put strike distance | 7.6 |
| 12 | **IV Rank** | Is protection cheap right now (time the buy) | 7.7 |
| 13 | **Protective-put payoff** | Cost, max loss, break-even — the instrument | 7.8 |
| 14 | **Theta** (Greek) | Daily cost of holding protection (drag awareness) | 7.1 |
| 15 | **Hedge-cost drag** | Protection spend as % — the budget cap | 7.10 |

**The vertical slice:** base book → Risk Score → protective-put hedge that steps in
and out. Single-leg puts (Level 1), one model, a scheduled loop — **no** collars, ES,
GARCH, PCA, VRP, or harness. Pull items up from §7.0.1 only as spare time appears.

### 7.0.1 The full metric catalog (everything, by tier)

The full quant toolkit, tagged **Core** (built for the submission) or **Stretch** (the
advanced "wow" layer, added if time allows). Bold rows are the upgrades that lift this
from intro-level to a real risk engine. Detail + worked examples follow in 7.1–7.13;
the Stretch items are specced in §7.14.

| Concept | What it does | Tier |
|---|---|---|
| Greeks (Δ, Γ, Θ, ν) | Option sensitivities — the vocabulary for everything | Core |
| Beta-weighted delta | The book's true market ($SPY-equiv) exposure | Core |
| **Downside (semi-)beta** | Exposure measured only in *down* markets — what we hedge | Core |
| Hedge ratio (delta-match) | Contracts needed for a target coverage % | Core |
| **OLS minimum-variance hedge ratio** | Statistically optimal hedge size (regression) | Core |
| **EWMA volatility** | Dynamic vol forecast (clustering), not a constant | Core |
| Value at Risk (VaR) | Loss threshold on a normal bad day | Core |
| **Fat-tail VaR (Cornish-Fisher / historical)** | VaR adjusted for skew + kurtosis | Core |
| **Expected Shortfall (ES / CVaR)** | Average loss *once* the tail breaks — coherent | Core |
| Stress test / scenario P&L | Loss under a −X% shock, hedged vs unhedged | Core |
| Expected move | IV-implied range → sensible strike distance | Core |
| IV Rank | Is protection cheap vs its own past year | Core |
| **Variance Risk Premium (implied − realized)** | The premium-selling edge, quantified | Core |
| **IV skew (put side)** | Cost of crash protection → skew-aware strikes | Core |
| Collar / put-spread payoff | Net cost, max loss/gain, break-evens | Core |
| Coverage ratio & hedge-cost drag | Discipline ratios (how much / how costly) | Core |
| Risk Score (0–100) | Composite that sets the hedge ratio | Core |
| Fills & liquidity model | Paper NBBO fill assumptions + liquidity gate | Core |
| GARCH(1,1) volatility | Richer vol forecast than EWMA (mean-reverting) | Stretch |
| Monte Carlo VaR | Simulated tail distribution vs. closed-form | Stretch |
| Conditional Drawdown-at-Risk (CDaR) | Path-dependent tail (peak-to-trough) | Stretch |
| Delta-gamma hedging | Convexity-aware hedge for large moves | Stretch |
| Component / marginal VaR | Where portfolio risk is concentrated | Stretch |
| Absorption Ratio (PCA) | Systemic-fragility gauge — are things coupling? | Stretch |
| Avg pairwise correlation / dispersion | Correlation-regime detection | Stretch |
| VIX term structure (contango/backwardation) | Cheap macro stress trigger | Stretch |
| Factor decomposition (mkt/size/value/mom) | Hedge factor exposures, not just index delta | Stretch |

### 7.1 The Greeks — what an option "feels"

Every option has four sensitivities. These are the vocabulary for everything else.

| Greek | Plain meaning | Why we care |
|------|----------------|-------------|
| **Delta (Δ)** | Price change per **$1** move in the stock. Calls 0→+1, puts 0→−1. | Delta ≈ "how many shares this option acts like." Also ≈ probability of finishing in‑the‑money. This is what we hedge. |
| **Gamma (Γ)** | How fast **delta itself** changes as the stock moves. | High gamma = your hedge drifts quickly = you must rebalance more often. |
| **Theta (Θ)** | Value lost **per day** from time decay. | When we *buy* a put, theta is a **cost**. When we *sell* a call, theta is **income**. |
| **Vega (ν)** | Price change per **1‑point** move in implied volatility (IV). | Protection *gains* value when fear spikes (IV up). Long options are vega‑positive. |

**Share‑equivalent exposure of one option** = `delta × 100`.
Example: a put with delta **−0.40** behaves like being **short 40 shares**
(`−0.40 × 100`).

### 7.2 Beta‑weighted delta — the book's true market exposure

Different stocks move by different amounts when the market moves. **Beta** measures
that: beta 1.2 means the stock tends to move 1.2% when the market moves 1%. To know
our real exposure we convert everything into **"SPY‑equivalent" dollars.**

```
Position market exposure ($)      = shares × price
Beta‑weighted exposure ($ vs SPY) = shares × price × beta
Portfolio delta ($)               = sum of beta‑weighted exposure across all positions
```

**Worked example.** We own 200 shares of a stock at $230, beta 1.2:

```
Market exposure       = 200 × $230           = $46,000
Beta‑weighted vs SPY  = $46,000 × 1.2         = $55,200
```

Meaning: if SPY drops **1%**, this position is expected to lose about
`$55,200 × 1% = $552`. Sum this across the whole book to get the portfolio's total
SPY‑equivalent exposure — say it comes to **$112,000**. That single number is what
we hedge.

> **Core upgrade — downside (semi-)beta.** Ordinary beta is an *average*; a hedger
> cares how the book behaves specifically when the market *falls*. **Downside beta**
> is the same regression run only over down-market days (market return < 0). For
> equities it's usually *higher* than plain beta, so plain beta **understates** the
> exposure we most need to hedge. We beta-weight the book with downside beta for
> sizing the hedge.

### 7.3 How much protection to buy — the hedge ratio

To offset the book's delta with put options:

```
Contracts for a FULL hedge = portfolio delta (in SPY‑equivalent shares)
                             ÷ ( |put delta| × 100 )
```

**Worked example.** Book exposure = **$112,000**, SPY at **$560**, so
SPY‑equivalent shares = `112,000 ÷ 560 = 200 shares`. We pick a put with delta
**−0.40**:

```
Full hedge = 200 ÷ (0.40 × 100) = 200 ÷ 40 = 5 contracts
```

We rarely hedge 100% (that's expensive and kills upside). For a **50% partial
hedge** we'd buy `5 × 0.50 ≈ 2–3 contracts`. **How much to cover (the fraction) is
exactly the judgment call the LLM makes** using the risk signals below.

> **Core upgrade — minimum-variance hedge ratio.** Delta-matching assumes the hedge
> moves 1:1 with the book; it doesn't. The statistically optimal amount is the
> regression (OLS) hedge ratio:
> ```
> h* = Cov(ΔBook, ΔHedge) ÷ Var(ΔHedge)  =  ρ × (σ_book ÷ σ_hedge)
> ```
> It's the slope of book returns on hedge returns — the hedge quantity that minimises
> the *variance* of the combined position. We use `h*` to scale the delta-based
> contract count; the LLM still sets what *fraction* of `h*` to actually deploy.

### 7.4 Value at Risk (VaR) — how bad is a *normal* bad day

VaR estimates the loss we'd expect to be exceeded only rarely (e.g. 1 day in 20).

```
Daily volatility = annual volatility ÷ √252
1‑day VaR ($)    = portfolio value × daily volatility × z
      z = 1.65 for 95% confidence,  z = 2.33 for 99%
```

**Worked example.** $100,000 book, 20% annual volatility:

```
Daily vol = 0.20 ÷ √252 = 0.20 ÷ 15.87 = 1.26%
95% VaR   = 100,000 × 0.0126 × 1.65 ≈ $2,079
99% VaR   = 100,000 × 0.0126 × 2.33 ≈ $2,936
```

Read as: "on a normal bad day (~1 in 20) we'd expect to lose **at least ~$2,079**
if unhedged." Rising VaR (because volatility jumped) is a **trigger to add
protection.**

> **Core upgrades — a real tail model.** The basic VaR above assumes constant, normal
> volatility. Markets are neither, and a hedger lives in the tail, so we sharpen all
> three parts:
>
> - **EWMA volatility** (RiskMetrics) — vol *clusters*, so forecast it instead of
>   using a trailing constant:  `σ²ₜ = λ·σ²ₜ₋₁ + (1−λ)·r²ₜ₋₁`,  λ ≈ 0.94.  This same
>   live vol feeds VaR, the expected move (§7.6), and the "turbulence building?" signal.
> - **Fat-tail VaR (Cornish-Fisher / historical).** Equity returns are left-skewed and
>   fat-tailed, so normal VaR *understates* crash risk. Cornish-Fisher bumps the
>   z-score for skewness + kurtosis; historical VaR just reads the empirical quantile
>   of real returns. Either pushes the number toward honesty.
> - **Expected Shortfall (ES / CVaR)** — VaR is only a *threshold*; ES is the **average
>   loss once you breach it** (`ES₉₅ = mean loss beyond the 95% VaR`). It's the
>   coherent, Basel-standard tail measure and the right thing for a hedger to target.
>
> **Illustrative feel.** With −0.7 skew and fat tails, a 99% VaR that normal math calls
> ~$2,936 lands nearer **~$3,600** under Cornish-Fisher, with **ES₉₉ ≈ $4,500** — the
> real bad-day pain is ~50% worse than the naive number. That gap *is* the reason to
> hedge, and why the basic VaR alone isn't enough.

### 7.5 Stress test — what a *shock* does, with and without the hedge

VaR covers normal days; stress tests cover crashes. Pick a scenario (e.g. market
**−5%**) and compute the damage both ways.

**Worked example.** Book delta = $112,000; scenario SPY **−5%**:

```
Unhedged loss      = $112,000 × −5%              = −$5,600
Hedge payoff       = puts gain roughly
                     (put delta × shares × move)
                   ≈ 5 contracts × 100 × 0.40 × ($560×5%)
                   = 500 × 0.40 × $28              = +$5,600  (near full offset)
Net with full hedge ≈ −$5,600 + $5,600           ≈  $0
Net with 50% hedge  ≈ −$5,600 + $2,800           ≈ −$2,800
```

(Real hedges do *better* than this in a crash because falling markets spike IV,
which lifts put value via **vega** — a bonus the linear estimate ignores.)

### 7.6 Expected move — the market's own priced‑in range

Implied volatility tells us how big a move the market is pricing. This sets sensible
strike distances (don't pay for protection miles away, don't buy uselessly close).

```
Expected move over T days = Stock price × IV × √(T ÷ 252)
```

**Worked example.** SPY $560, IV 18%, T = 30 days:

```
Expected move = 560 × 0.18 × √(30 ÷ 252)
              = 560 × 0.18 × √0.119
              = 560 × 0.18 × 0.345 ≈ $34.8   (about ±6.2%)
```

So over the next month the market expects roughly **±$35**. A protective put around
**$525** (one expected‑move down) is a natural, non‑wasteful strike.

### 7.7 IV Rank — is protection cheap or expensive *today*

Buying protection when it's cheap is a big part of the edge. IV Rank places today's
IV inside its own past‑year range:

```
IV Rank = (current IV − 1‑yr low IV) ÷ (1‑yr high IV − 1‑yr low IV) × 100
```

**Worked example.** Current IV 18%, 1‑yr low 12%, 1‑yr high 40%:

```
IV Rank = (18 − 12) ÷ (40 − 12) × 100 = 6 ÷ 28 × 100 ≈ 21%
```

Low IV Rank (~21%) → **insurance is cheap → good time to buy puts.** High IV Rank →
protection is expensive → prefer **spreads/collars** (where we also *sell* pricey
premium) or wait. This is the agent's *"when to step in"* signal.

> **Core upgrades — price the edge, not just the rank.**
> - **Variance Risk Premium (VRP) = implied − realized variance** (or IV − realized
>   vol). This *is* our edge as a number: when implied sits well above what actually
>   happened, premium is genuinely overpriced and selling it pays. IV Rank says "high
>   vs its own year"; VRP says "overpriced vs *reality*" — a better sell/hold trigger.
> - **IV skew (put side).** The vol surface isn't flat — downside puts trade at higher
>   IV than ATM. The **skew** (e.g. 25-delta put IV − ATM IV) says how *expensive crash
>   protection is right now*, so we pick strikes off the skew instead of a flat
>   "one expected-move down." Steep skew → protection is dear → prefer spreads/collars.

### 7.8 Collar math — protection that pays for itself

A **collar** on stock we own = **buy a put** (protection) + **sell a call** (income).

```
Net cost      = put premium − call premium     (often ≈ 0 or a credit)
Max loss      = (stock − put strike) × 100 + net cost      (below the put)
Max gain      = (call strike − stock) × 100 − net cost     (above the call)
```

**Worked example.** 100 shares of SPY at $560:

```
Buy  $540 put  @ $8.00  →  −$800   (protection)
Sell $580 call @ $6.00  →  +$600   (income)
Net cost = $800 − $600  =   $200    (just 0.36% of the $56,000 position)

Max loss  = ($560 − $540)×100 + $200 = $2,000 + $200 = $2,200  (≈ 3.9%)
Max gain  = ($580 − $560)×100 − $200 = $2,000 − $200 = $1,800
```

For $200 we've capped a $56k position's downside at ~3.9% — that's the collar doing
the heavy lifting cheaply.

### 7.9 Put‑spread math — cheaper, capped protection

When we want protection but not the full put premium: **buy a put, sell a
further‑out put.**

```
Cost (net debit)   = bought‑put premium − sold‑put premium
Max protection ($) = (higher strike − lower strike) × 100 − net debit
```

**Worked example.** Buy $540 put @ $8.00, sell $520 put @ $4.00:

```
Net debit      = $800 − $400 = $400
Max protection = ($540 − $520) × 100 − $400 = $2,000 − $400 = $1,600
```

Half the cost of the outright put ($400 vs $800), but protection only works between
$540 and $520. Good when a *catastrophic* crash is unlikely and we just want to
blunt an ordinary drop.

### 7.10 Cost, coverage, and when to release

Two ratios keep the whole book disciplined:

```
Coverage ratio  = hedged exposure ÷ total exposure        (target set by the LLM, e.g. 40–70%)
Hedge cost drag = net premium spent ÷ portfolio value      (keep under a budget, e.g. ≤ ~1%/month)
```

**Release / roll rules (deterministic):**
- **Release** a hedge when risk normalizes — IV Rank falls back down, beta‑weighted
  delta drift is small, and any feared catalyst has passed.
- **Roll** a hedge when it's near expiry (e.g. **DTE < 7**) or the put's delta has
  decayed so far it no longer protects.
- Always respect the risk‑gate caps: **≤ 2% premium per trade**, **≤ 30% of the
  account** deployed in option premium at once, **≤ 5% per single underlying**, and a
  **−5% daily‑loss halt** that benches the agent for the rest of the day.

### 7.11 How the numbers drive the agent (putting it together)

Each cycle the math engine emits a compact snapshot, e.g.:

```
portfolio_delta = +$112,000 (SPY‑equiv)
95%_1d_VaR      = $2,079      (up 18% vs yesterday)
stress_-5%      = −$5,600 unhedged / −$2,800 at current 50% coverage
IV_Rank         = 21%         (protection is cheap)
coverage_now    = 50%
hedge_cost_drag = 0.4%/month
catalyst        = CPI print in 2 days
```

The LLM reads that and decides, in words with reasons:
> *"VaR is rising and there's a CPI print in two days, but IV Rank is low so
> protection is cheap. Step coverage up from 50% → 70% using a $540/$520 put
> spread (cheap, defined risk), keep the collar. Re‑evaluate after CPI."*

Deterministic code then sizes and places it, and logs the whole thing for
self‑grading. **Numbers measure; the model judges; the log remembers.**

### 7.12 The Risk Score — one dial the whole hedge follows

All the numbers above roll up into a single **Risk Score (0–100)** that sets the
**hedge ratio** (how much of the book to protect). Calm = low score = little or no
hedge (zero drag); stress = high score = step protection up. Two groups of inputs:

**Portfolio risk (math on our own account):**
- **Drawdown from peak** — how far the book is off its high‑water mark
- **Beta‑weighted net delta** (§7.2) — the book's true market exposure
- **Concentration** — single‑name / sector overweight
- **Current coverage** (§7.10) — how much protection is already on

**Market‑stress radar (the "when to step in" signals):**
- **VIX level + spike** — the fear gauge; rising fast = step in
- **Regime** — SPY below its 20/50‑day average = risk‑off
- **Realized vs. implied vol** — is turbulence actually building?
- **Correlation spike** — everything moving together = systemic risk (diversification
  fails exactly when you need it)
- **Breadth deterioration** — fewer names holding up
- **Rising VaR / a scheduled catalyst** (§7.4, §7.6)

```
hedge ratio  ∝  Risk Score     (e.g. score < 25 → 0% ·  25–60 → partial ·  > 60 → full)
Risk Score rises → step in  → buy / scale the hedge
Risk Score falls → release  → unwind it to stop premium bleed
```

The math computes the score; the **LLM decides whether the score justifies acting
right now**, given soft context. That step‑in / release discipline *is* the track.

### 7.13 Fills & liquidity (paper‑trading reality)

We *sell* premium (short call in a collar, short leg of a put spread), so a fair
worry is "who buys it on a paper account?" **Nobody real has to** — paper trading
doesn't match us to a counterparty; it **simulates fills against the real live market
quotes (NBBO).** Practical rules that follow:

- **Sell at or through the bid** (market or marketable‑limit orders) → fills reliably
  at ~the real bid. A limit *above* the bid may sit unfilled until the real quote
  moves to it — just like live.
- **Liquid underlyings only** — SPY / QQQ / mega‑caps with tight, active quotes. On
  illiquid contracts the real quote is wide/stale and paper fills get unreliable.
  This *is* the liquidity gate.
- **Marking & expiration use real prices**, so the premium‑selling edge genuinely
  shows up in paper P&L — the strategy is truly testable, not fake.
- **Paper is slightly optimistic** — it fills at the quote with no slippage / market
  impact modeled, so live P&L runs a touch worse. Negligible at our small sizes on
  liquid names; confirm real fill behavior on the **dev account (Aug 18–28)**.

### 7.14 The Stretch layer (build last, if time allows)

One line each — these turn a solid risk engine into an institutional-grade one:

- **GARCH(1,1) volatility** — `σ²ₜ = ω + α·r²ₜ₋₁ + β·σ²ₜ₋₁`; a vol forecast with
  mean-reversion, a step beyond EWMA.
- **Monte Carlo VaR** — simulate thousands of return paths from the fitted
  vol/correlations and read the tail, instead of a closed-form quantile.
- **Conditional Drawdown-at-Risk (CDaR)** — the expected worst peak-to-trough drawdown
  in the tail; path-dependent, closer to felt pain than 1-day VaR.
- **Delta-gamma hedging** — hedge `ΔV ≈ Δ·ΔS + ½·Γ·ΔS²`, not just delta, so the hedge
  holds up on *large* moves.
- **Component / marginal VaR** — split total VaR into each position's contribution
  (`CVaRᵢ = wᵢ · ∂VaR/∂wᵢ`); hedge the concentrated names first.
- **Absorption Ratio (Kritzman–Li)** — fraction of the book's variance explained by its
  top PCA eigenvectors; a spike = everything coupling = fragile market = step in.
- **Avg pairwise correlation / dispersion** — a proper coupling gauge for the stress radar.
- **VIX term structure** — VIX futures in backwardation (front > back) is a strong,
  cheap risk-off trigger.
- **Factor decomposition** — regress the book on market/size/value/momentum factors and
  hedge the factor exposures, not just index delta.

---

## 8. Proving it works — the crash backtest & the demo

Five live days is noise, and a calm week won't show the hedge working. So we **prove
the value with a backtest**, and build the demo around *defense*, not raw P&L.

**The crash backtest (video centerpiece).** Replay the signal + hedge engine through
real historical stress — **COVID Mar‑2020, the 2022 bear, the Aug‑2024 vol spike** —
and show two equity curves: **unhedged vs. hedged.** The hedge visibly cushions the
drop. This is what proves the agent works even if the live window is quiet.
> Data note: Alpaca option greeks/IV are snapshot‑only (no history), so we backtest
> the **signals + hedge decisions on historical price/vol** and approximate the
> option payoffs.

**The three "wow" features (all defense‑framed):**
1. **Adaptive hedge timeline** — a visual of the agent *stepping in* as risk rose and
   *releasing* as it fell, with the logged reasoning at each step. The money shot.
2. **Hedged‑vs‑unhedged crash curves** — the backtest above.
3. **Risk‑regime reasoning + self‑grading** — the agent explains *why* it hedged, then
   grades whether the protection was worth its cost ("bought insurance, market held —
   small drag, correct discipline" vs. "hedge saved 8%").

**Where we score** (judging is 5 criteria: P&L, Tech, Creativity, Presentation,
Social): lead with **risk‑adjusted metrics** (max drawdown, volatility, Sharpe) where
a hedger genuinely wins; a "cost of protection vs. drawdown avoided" dashboard; and a
live risk‑regime gauge. Creativity + Presentation are our strongest categories here.

---

## 9. Build milestones

1. **Setup** — keys, Alpaca paper account (options enabled; request **Level 3** for
   spreads early — approval can lag), MCP server running, seed the base book (SPY/QQQ).
2. **Risk engine** — portfolio risk + stress signals → the **Risk Score** (§7.12), all
   deterministic math.
3. **Hedge gate** — hedge‑ratio rules, collar / put‑spread construction, step‑in /
   release logic, risk caps.
4. **LLM layer** — Kimi K3 reads the Risk Score + news and decides posture, with
   reasons (via the `agent/llm.py` seam).
5. **Adaptive loop** — wire observe→measure→decide→execute→monitor on the heartbeat /
   event runtime (§6).
6. **Backtest** — hedged vs. unhedged through a historical crash (the video centerpiece).
7. **Self‑grading + dashboards** — cost‑vs‑protection, risk‑regime gauge, timeline.
8. **Demo polish + fresh paper account swap + build‑in‑public posts.**

---

## 10. Locked / open decisions

**Locked (this doc):**
- **Track 3** — Hedging & Risk Protection, run as *risk‑managed premium harvesting*.
- **Core book** = a defined basket of stocks/ETFs the agent protects (not
  discretionary stock‑picking).
- **Models** — Kimi K3 (reasoning) + Gemma 4 31B on Cerebras (fast lane), paid HF,
  cost non‑issue.
- **Runtime** — GitHub Actions scheduled workflow (30‑min heartbeat, state committed
  back to the repo); always‑on/event‑driven (Modal) deferred to post‑hackathon.
- **Profit engine** — core returns + financed collars + selling overpriced premium.
- **Math engine** — §7 above, all deterministic.

**Open (need Yugo / a decision):**
- **Harness** — Hermes/OpenClaw *only if Yugo already runs it and owns integration*;
  otherwise our own lean loop.
- **The core book** — finalize the exact tickers (liquid, optionable mega‑caps +
  a couple of ETFs).
- **Coverage bands** — default min/max coverage % and the hedge‑cost budget.
- **Exact model ids** — confirm against the HF Inference Providers list.

---

## 11. One‑paragraph summary (for the pitch)

*Our agent runs a real portfolio and protects it like a disciplined risk manager.
Hard math measures the book's exposure, its value‑at‑risk, and whether insurance is
cheap right now; a strong reasoning model (Kimi K3) reads those numbers plus live
news and decides when to step in, how much to protect, and when to lift a hedge —
using collars and defined‑risk spreads that mostly pay for themselves. It runs
continuously, reacts to events, caps every downside by design, and writes down its
reasoning so it can grade itself. It never guesses which way the market will go —
it just makes sure a bad day can't hurt us, and quietly collects premium while it
waits.*

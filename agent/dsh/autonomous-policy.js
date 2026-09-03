// Reviewed paper-only option universe. Keep this in lockstep with
// agent.candidates.AUTONOMOUS_OPTION_UNDERLYINGS; both boundaries reject
// symbols outside this list.
export const AUTONOMOUS_OPTION_UNDERLYINGS = Object.freeze([
  'SPY', 'QQQ', 'IWM', 'DIA', 'XLK', 'XLF', 'XLV', 'SMH',
  'AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'AMD', 'TSLA',
])

export const AUTONOMOUS_OPTIONS_SYMBOLS = new Set(AUTONOMOUS_OPTION_UNDERLYINGS)
export const AUTONOMOUS_OPTIONS_STRUCTURES = new Set([
  'protective_put', 'covered_call', 'iron_condor', 'bull_put_spread', 'bear_call_spread',
])

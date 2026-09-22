# 02: RSI, MACD and volatility analysts

**What to build:** Three more technical analysts on the same `AnalystSignal`: RSI (14-day; overbought and oversold readings explained), MACD (12/26/9; crossovers and their age), and volatility (20-day annualised against the listing's own past year, high volatility lowering confidence rather than implying direction). `oracle analyse AAPL` prints all technical signals plus a combined technicals line (a confidence-weighted average, rule documented).

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] Each indicator matches a hand-checked reference series to 4 decimal places
- [ ] Oversold RSI reads as a possible bounce, not a buy instruction
- [ ] A MACD crossover reports how many days ago it happened
- [ ] Gaps in bars (holidays) never break the indicators
- [ ] The combined line shows which analysts it combined and how

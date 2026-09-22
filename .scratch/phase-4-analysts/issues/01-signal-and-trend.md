# 01: Analyst signal type and a trend analyst, end to end

**What to build:** `oracle analyse AAPL` runs the trend analyst over the listing's stored daily bars and prints its signal in plain English. Introduces the typed `AnalystSignal` every later analyst emits: analyst name, symbol, as-of date (the last bar's date), direction (bearish, neutral, bullish), a score from -1 to 1, a confidence from 0 to 1, a one-sentence plain-English reason, and the named figures behind it. Signals are stored per symbol, analyst and data date (the last bar's): rerunning on unchanged data replaces the signal, a new bar adds history. Trend compares the close with its 50- and 200-day averages and the 12-1 month return (conservative, documented defaults). Analysts are pure functions over stored data; the service loads data and stores results; nothing is fetched.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] Rising, falling and flat synthetic series give bullish, bearish and neutral signals
- [x] Too little history (under a year, 253 bars, which the 12-1 return needs) gives no signal and says why, never a guess
- [x] A signal names the figures it used, and its reason reads without jargon
- [x] A rerun on unchanged data replaces the stored signal; a new bar adds a new one
- [x] `oracle analyse` refuses unknown symbols with suggestions, and says to run `prices update` when no bars are stored
- [x] Architecture test still passes (analysts live under services' reach, never import CLI or GUI)

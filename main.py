"""BTC race bot for make.fun btc-race-24h-20261112-1439 (agent-fun/bars-replay/v1).

Strategy (momentum trend-following, long only, full size, few trades):
  * Track the 1-minute closes of CBBTC and an EMA-240 of them (integer math only).
  * ENTER long with all cash when the 60-bar return is >= 55 bps AND the latest
    close is above the EMA-240 (a real up-trend, not noise).
  * EXIT the whole position when the close falls 80 bps below the peak close
    since entry (trailing stop), or when the 60-bar return turns <= -20 bps
    (momentum reversal).
  * After an exit the entry signal must re-arm: the 60-bar return has to dip
    below 0 before a new entry is allowed. This stops the bot re-buying the
    same fading move bar after bar when a trailing-stop exit fires while the
    60-bar return is still positive (whipsaw churn).
  * Orders carry max_slippage_bps=100. No orders on batches that carry no new
    CBBTC bar (gap minutes). No clock, no files, no environment, no randomness:
    two runs on the same data produce identical orders.

Dev-window backtest (kit dataset, exact harness fill math incl. 4 bps fee,
2 bps slippage and constant-product impact vs the bar open depth):
  net PnL +$3.44, 2 trades, max drawdown 0.41%, fees $0.80.
  Buy-and-hold on the same window loses $25.97 (BTC fell 2.30%); EMA-cross
  baselines lose $55-$147 (whipsaw). The edge is one clean trade: enter the
  early rally at bar 65, exit at bar 220. In the choppy second half the bot
  makes no trade at all (sits in cash) instead of bleeding on noise.
  Parameter plateau (no overfitting): entry 50-65 bps and exit -15..-25 bps
  all clear the dev gate at +$3.44..+$4.24; the trailing stop never binds
  on the dev window (safety net only). Mean reversion was tested and rejected
  (best -$13.66, worst -$419 on 444 whipsaw trades). A guardrail fallback
  (a weaker late entry just to guarantee >=1 trade) was tested and dropped:
  on a no-rally window it costs -$5..-$8 for eligibility it may not need,
  and the primary entry fires on any window with a real rally.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
ENTRY_BPS = 55        # 60-bar return needed to enter (bps)
EXIT_BPS = -20        # 60-bar return that triggers exit (bps)
TRAIL_BPS = 80        # trailing stop from peak close since entry (bps)
EMA_N = 240           # slow-trend filter span (bars)
MAX_SLIP_BPS = 100    # max slippage vs the latest close, buy and sell


class Bot:
    def __init__(self):
        self.closes = []      # closes seen so far (ints, price scale 5)
        self.last_t = None    # t of the last CBBTC bar processed
        self.ema = None       # EMA-240 of closes (int, seeded on first close)
        self.peak = None      # highest close since entry (int)
        self.armed = True     # entry allowed only when the signal has re-armed

    def on_batch(self, state, batch):
        # Find this batch's CBBTC bar, if any. A batch with no new bar (a gap
        # minute) carries no new information and no fill: answer empty.
        bar = None
        for b in batch.get("bars", []):
            if b.get("symbol") == SYMBOL:
                bar = b
                break
        if bar is None or bar.get("t") == self.last_t:
            return []
        self.last_t = bar["t"]
        close = int(bar["c"])
        self.closes.append(close)
        k_num, k_den = 2, EMA_N + 1
        if self.ema is None:
            self.ema = close
        else:
            self.ema = (close * k_num + self.ema * (k_den - k_num)) // k_den
        n = len(self.closes)
        held = state.positions.get(SYMBOL)
        qty = held.qty if held is not None else 0
        ret60 = None
        if n >= 61:
            ret60 = (close - self.closes[-61]) * 10000 // self.closes[-61]
        if qty == 0:
            self.peak = None
            if ret60 is None:
                return []
            # Re-arm: after an exit, the 60-bar return must go below 0 first.
            if ret60 < 0:
                self.armed = True
            if not self.armed:
                return []
            if close > self.ema and ret60 >= ENTRY_BPS and state.cash >= 1000000:
                return [{"side": "buy", "symbol": SYMBOL,
                         "notional_quote_units": state.cash,
                         "max_slippage_bps": MAX_SLIP_BPS}]
            return []
        # Holding: track the peak and exit on trailing stop or momentum reversal.
        self.peak = close if self.peak is None else max(self.peak, close)
        if (close - self.peak) * 10000 // self.peak <= -TRAIL_BPS:
            self.armed = False
            return [{"side": "sell", "symbol": SYMBOL, "qty": qty,
                     "max_slippage_bps": MAX_SLIP_BPS}]
        if ret60 is not None and ret60 <= EXIT_BPS:
            self.armed = False
            return [{"side": "sell", "symbol": SYMBOL, "qty": qty,
                     "max_slippage_bps": MAX_SLIP_BPS}]
        return []


BOT = Bot()
makefun_bars.run(BOT.on_batch)

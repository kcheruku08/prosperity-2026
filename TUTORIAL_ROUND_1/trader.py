from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List


# EMERALDS-only strategy.
#
# Structural facts from the data:
#   * Mid is stationary at 10000 (>97% of ticks); tail prints at 9996/10004.
#   * The visible book sits at 9992/10008 almost always (16-tick spread).
#   * Fair value = 10000.
#
# Two alpha sources:
#   1. TAKE: any ask <= 9999 or any bid >= 10001 is printed money. Sweep every
#      level of the book that crosses fair by >= 1 tick.
#   2. MAKE: post passive quotes inside the 9992/10008 book. Per-fill edge
#      scales with quote distance from fair, so we quote as wide as we safely
#      can while staying inside the book and off the mid-print outliers at
#      9996/10004. BASE_EDGE = 3 → 9997/10003.
#
# Inventory control: when we're long, widen the bid and tighten the ask so
# we skew toward flat; symmetric when short. The quote is never allowed on
# the wrong side of fair.

SYMBOL = "EMERALDS"
LIMIT = 20
FAIR = 10000
TAKE_EDGE = 1
BASE_EDGE = 3


class Trader:
    def run(self, state: TradingState):
        orders: Dict[str, List[Order]] = {}
        od = state.order_depths.get(SYMBOL)
        if od is None:
            return orders, 0, ""

        pos = state.position.get(SYMBOL, 0)
        buy_cap = LIMIT - pos
        sell_cap = LIMIT + pos
        out: List[Order] = []

        # 1. TAKE every crossing level in one sweep.
        if od.sell_orders and buy_cap > 0:
            for px in sorted(od.sell_orders):
                if px > FAIR - TAKE_EDGE or buy_cap <= 0:
                    break
                qty = min(-od.sell_orders[px], buy_cap)
                if qty > 0:
                    out.append(Order(SYMBOL, px, qty))
                    buy_cap -= qty

        if od.buy_orders and sell_cap > 0:
            for px in sorted(od.buy_orders, reverse=True):
                if px < FAIR + TAKE_EDGE or sell_cap <= 0:
                    break
                qty = min(od.buy_orders[px], sell_cap)
                if qty > 0:
                    out.append(Order(SYMBOL, px, -qty))
                    sell_cap -= qty

        # 2. MAKE with inventory-scaled edges.
        # inv_frac in [-1, 1]; positive = long => widen bid, tighten ask.
        inv_frac = pos / LIMIT
        bid_edge = max(1, round(BASE_EDGE * (1 + inv_frac)))
        ask_edge = max(1, round(BASE_EDGE * (1 - inv_frac)))
        my_bid = FAIR - bid_edge
        my_ask = FAIR + ask_edge

        if buy_cap > 0:
            out.append(Order(SYMBOL, my_bid, buy_cap))
        if sell_cap > 0:
            out.append(Order(SYMBOL, my_ask, -sell_cap))

        orders[SYMBOL] = out
        return orders, 0, ""

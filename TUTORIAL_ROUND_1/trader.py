from datamodel import Order, OrderDepth, TradingState, Symbol
from typing import Dict, List, Tuple
import json


# Two product archetypes observed in the data:
#
#   EMERALDS: stationary. Mid visits {9996, 10000, 10004}; book persists at
#   9992/10008 (16-tick spread). There is a true anchored fair value.
#
#   TOMATOES: non-stationary random walk. Mid drifts over tens of points;
#   price autocorr ~= 1, return autocorr is just bid-ask bounce. No anchored
#   fair exists — recent mid is the best estimate of next mid.
#
# Shared rules:
#   1. Quote inside the visible book when possible; never quote on the wrong
#      side of our fair estimate.
#   2. Take opportunistically when the book crosses our fair by >= 1 tick.
#   3. Skew quotes against inventory so we drift back to flat.


POSITION_LIMIT = {"EMERALDS": 20, "TOMATOES": 20}

EMERALDS_FAIR = 10000
EMERALDS_TAKE_EDGE = 1  # take any ask <= 9999 or bid >= 10001
EMERALDS_MAKE_EDGE = 2  # conservative: inside the 16-tick book but off the outlier prints

TOMATOES_EMA_ALPHA = 0.05   # ~20-tick half life; tracks drift without chasing noise
TOMATOES_TAKE_EDGE = 2
TOMATOES_MAKE_EDGE = 3      # structural: ~half of the typical 13-tick spread, inside the book


def best_bid_ask(od: OrderDepth) -> Tuple[int, int]:
    best_bid = max(od.buy_orders.keys()) if od.buy_orders else None
    best_ask = min(od.sell_orders.keys()) if od.sell_orders else None
    return best_bid, best_ask


class Trader:
    def run(self, state: TradingState):
        memory: Dict = {}
        if state.traderData:
            try:
                memory = json.loads(state.traderData)
            except Exception:
                memory = {}

        orders: Dict[Symbol, List[Order]] = {}

        for symbol, od in state.order_depths.items():
            pos = state.position.get(symbol, 0)
            limit = POSITION_LIMIT.get(symbol, 20)

            if symbol == "EMERALDS":
                orders[symbol] = self._make_market(
                    symbol, od, pos, limit,
                    fair=EMERALDS_FAIR,
                    take_edge=EMERALDS_TAKE_EDGE,
                    make_edge=EMERALDS_MAKE_EDGE,
                )
            elif symbol == "TOMATOES":
                best_bid, best_ask = best_bid_ask(od)
                if best_bid is None or best_ask is None:
                    continue
                mid = (best_bid + best_ask) / 2
                prev_ema = memory.get("tomatoes_ema", mid)
                fair = TOMATOES_EMA_ALPHA * mid + (1 - TOMATOES_EMA_ALPHA) * prev_ema
                memory["tomatoes_ema"] = fair

                orders[symbol] = self._make_market(
                    symbol, od, pos, limit,
                    fair=fair,
                    take_edge=TOMATOES_TAKE_EDGE,
                    make_edge=TOMATOES_MAKE_EDGE,
                )

        return orders, 0, json.dumps(memory)

    def _make_market(
        self,
        symbol: Symbol,
        od: OrderDepth,
        pos: int,
        limit: int,
        fair: float,
        take_edge: int,
        make_edge: int,
    ) -> List[Order]:
        """Generic MM: take obvious crosses, then post passive quotes anchored at fair.

        Quotes are placed inside the visible book when possible (improve by 1 tick)
        but never on the wrong side of fair. Inventory skew shifts the centre
        against current position so we drift back to flat.
        """
        out: List[Order] = []
        buy_cap = limit - pos
        sell_cap = limit + pos

        # 1. Aggressive takes vs visible book
        for ask_px in sorted(od.sell_orders):
            if ask_px <= fair - take_edge and buy_cap > 0:
                qty = min(-od.sell_orders[ask_px], buy_cap)
                out.append(Order(symbol, ask_px, qty))
                buy_cap -= qty
            else:
                break

        for bid_px in sorted(od.buy_orders, reverse=True):
            if bid_px >= fair + take_edge and sell_cap > 0:
                qty = min(od.buy_orders[bid_px], sell_cap)
                out.append(Order(symbol, bid_px, -qty))
                sell_cap -= qty
            else:
                break

        # 2. Passive make, centred on fair with inventory skew
        skew = -pos / limit  # in [-1, 1]; shifts quotes away from current inventory
        centre = fair + skew

        my_bid = int(round(centre - make_edge))
        my_ask = int(round(centre + make_edge))

        # Improve on the visible book rather than sitting behind it.
        best_bid, best_ask = best_bid_ask(od)
        if best_bid is not None and best_bid >= my_bid:
            my_bid = best_bid + 1
        if best_ask is not None and best_ask <= my_ask:
            my_ask = best_ask - 1

        # Never cross fair with a passive quote — stay on the correct side.
        if my_bid >= fair:
            my_bid = int(fair) - 1
        if my_ask <= fair:
            my_ask = int(fair) + 1

        if buy_cap > 0:
            out.append(Order(symbol, my_bid, buy_cap))
        if sell_cap > 0:
            out.append(Order(symbol, my_ask, -sell_cap))

        return out

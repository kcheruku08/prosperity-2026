from datamodel import Order, OrderDepth, TradingState, Symbol
from typing import Dict, List
import json


POSITION_LIMIT = {"EMERALDS": 20, "TOMATOES": 20}

EMERALDS_FAIR = 10000
EMERALDS_TAKE_EDGE = 1
EMERALDS_MAKE_EDGE = 2

TOMATOES_WINDOW = 100
TOMATOES_MAKE_EDGE = 5
TOMATOES_TAKE_EDGE = 2
TOMATOES_Z_SKEW = 0.5  # shift quote centre by -Z_SKEW * zscore (fade deviations softly)


def best_bid_ask(od: OrderDepth):
    best_bid = max(od.buy_orders.keys()) if od.buy_orders else None
    best_ask = min(od.sell_orders.keys()) if od.sell_orders else None
    return best_bid, best_ask


class Trader:
    def run(self, state: TradingState):
        orders: Dict[Symbol, List[Order]] = {}

        memory = {}
        if state.traderData:
            try:
                memory = json.loads(state.traderData)
            except Exception:
                memory = {}
        mid_history: List[float] = memory.get("tomato_mids", [])

        for symbol, od in state.order_depths.items():
            pos = state.position.get(symbol, 0)
            limit = POSITION_LIMIT.get(symbol, 20)

            if symbol == "EMERALDS":
                orders[symbol] = self._trade_emeralds(od, pos, limit)
            elif symbol == "TOMATOES":
                orders[symbol], mid_history = self._trade_tomatoes(
                    od, pos, limit, mid_history
                )

        memory["tomato_mids"] = mid_history[-TOMATOES_WINDOW:]
        return orders, 0, json.dumps(memory)

    def _trade_emeralds(self, od: OrderDepth, pos: int, limit: int) -> List[Order]:
        orders: List[Order] = []
        fair = EMERALDS_FAIR
        buy_cap = limit - pos
        sell_cap = limit + pos

        # Aggressive takes: any ask strictly below fair, any bid strictly above fair
        for ask_px in sorted(od.sell_orders.keys()):
            if ask_px <= fair - EMERALDS_TAKE_EDGE and buy_cap > 0:
                ask_vol = -od.sell_orders[ask_px]  # sell_orders volumes are negative
                qty = min(ask_vol, buy_cap)
                orders.append(Order("EMERALDS", ask_px, qty))
                buy_cap -= qty
            else:
                break

        for bid_px in sorted(od.buy_orders.keys(), reverse=True):
            if bid_px >= fair + EMERALDS_TAKE_EDGE and sell_cap > 0:
                bid_vol = od.buy_orders[bid_px]
                qty = min(bid_vol, sell_cap)
                orders.append(Order("EMERALDS", bid_px, -qty))
                sell_cap -= qty
            else:
                break

        # Passive make: undercut book, sit inside 9992/10008
        best_bid, best_ask = best_bid_ask(od)
        make_bid = fair - EMERALDS_MAKE_EDGE
        make_ask = fair + EMERALDS_MAKE_EDGE
        if best_bid is not None and best_bid >= make_bid:
            make_bid = best_bid + 1
        if best_ask is not None and best_ask <= make_ask:
            make_ask = best_ask - 1

        if buy_cap > 0 and make_bid < fair:
            orders.append(Order("EMERALDS", make_bid, buy_cap))
        if sell_cap > 0 and make_ask > fair:
            orders.append(Order("EMERALDS", make_ask, -sell_cap))

        return orders

    def _trade_tomatoes(self, od: OrderDepth, pos: int, limit: int, history: List[float]):
        orders: List[Order] = []
        best_bid, best_ask = best_bid_ask(od)
        if best_bid is None or best_ask is None:
            return orders, history

        mid = (best_bid + best_ask) / 2
        history = history + [mid]
        history = history[-TOMATOES_WINDOW:]

        # Rolling fair value and z-score of current mid vs that mean
        n = len(history)
        fair = sum(history) / n
        if n >= 5:
            var = sum((x - fair) ** 2 for x in history) / n
            sd = var ** 0.5
        else:
            sd = 0.0
        zscore = (mid - fair) / sd if sd > 0 else 0.0

        buy_cap = limit - pos
        sell_cap = limit + pos

        # Aggressive take only when book clearly mis-quotes vs rolling fair
        if best_ask <= fair - TOMATOES_TAKE_EDGE and buy_cap > 0:
            ask_vol = -od.sell_orders[best_ask]
            qty = min(ask_vol, buy_cap)
            orders.append(Order("TOMATOES", best_ask, qty))
            buy_cap -= qty

        if best_bid >= fair + TOMATOES_TAKE_EDGE and sell_cap > 0:
            bid_vol = od.buy_orders[best_bid]
            qty = min(bid_vol, sell_cap)
            orders.append(Order("TOMATOES", best_bid, -qty))
            sell_cap -= qty

        # Inventory skew + soft z-score fade: centre shifts against position
        # and slightly against current deviation from rolling fair.
        inv_skew = -pos / limit  # in [-1, 1]
        z_skew = -TOMATOES_Z_SKEW * zscore
        centre = fair + inv_skew + z_skew
        make_bid = int(round(centre - TOMATOES_MAKE_EDGE))
        make_ask = int(round(centre + TOMATOES_MAKE_EDGE))

        # Stay inside existing book
        if make_bid >= best_ask:
            make_bid = best_ask - 1
        if make_ask <= best_bid:
            make_ask = best_bid + 1

        if buy_cap > 0:
            orders.append(Order("TOMATOES", make_bid, buy_cap))
        if sell_cap > 0:
            orders.append(Order("TOMATOES", make_ask, -sell_cap))

        return orders, history

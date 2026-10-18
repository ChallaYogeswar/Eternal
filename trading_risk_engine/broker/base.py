"""
Broker interface. `PaperBroker` and `KiteConnectBroker` are the only two
implementations, and paper_trader.py / the MCP layer never care which one
they're holding — the interface is exactly the same either way, which is
deliberate: swapping paper for live should be a configuration change, not
a code change (see gateway.py for how the two are actually kept apart —
NOT by different function calls, but by the SAME call requiring different
gates depending on which broker is active).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from ..models import Order, OrderSide, OrderType, Position


class Broker(ABC):
    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType,
        limit_price: Optional[float] = None,
        reference_price: Optional[float] = None,
        tag: str = "",
    ) -> Order:
        """`reference_price` is the price the order is evaluated/filled
        against. For PaperBroker this stands in for "the current market
        price" (there's no live feed pushing fills to a simulator, so the
        caller supplies it — typically the latest close or quote). For
        KiteConnectBroker it is unused; the real exchange determines the
        fill."""

    @abstractmethod
    def get_positions(self) -> List[Position]:
        ...

    @abstractmethod
    def get_margins(self) -> dict:
        """Returns at minimum {'cash': float, 'equity': float}."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        ...

    @abstractmethod
    def get_order(self, order_id: str) -> Optional[Order]:
        ...

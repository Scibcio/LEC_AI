from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Record:
    sku: str
    source: str
    qty: Optional[int]
    price: Optional[float]
    last_updated: datetime
    reserved: Optional[int] = None  # only shop reports this; None for every other source
    lead_time_days: Optional[int] = None  # only supplier reports this; None for every other source


@dataclass(frozen=True)
class Conflict:
    sku: str
    type: str  # "stock" or "price"
    claims: list  # every Record involved, however many sources reported one


@dataclass(frozen=True)
class Action:
    sku: str
    type: str  # RESTOCK, PRICE_ADJUST, ALERT, or ESCALATE_HUMAN
    priority: float  # severity * confidence * urgency
    category: str  # "overselling", "underselling", "price", or "internal" (drives severity/urgency)
    reason: str

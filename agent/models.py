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
    type: str  # "stock", "price", or "coverage" (reference source has no record at all)
    claims: list  # every Record involved, however many sources reported one


def field_for(conflict):
    """Which Record field this conflict is actually about. Single definition so
    the resolver, the learner and the ranker can't drift apart on it."""
    return "price" if conflict.type == "price" else "qty"


@dataclass(frozen=True)
class Action:
    sku: str
    type: str  # RESTOCK, PRICE_ADJUST, ALERT, or ESCALATE_HUMAN
    priority: float  # severity * urgency, damped by confidence
    category: str  # "coverage", "overselling", "underselling", "price", or "internal"
    reason: str
    confidence: float = 0.0  # resolver's relative margin, reported alongside priority, not folded into it
    summary: str = ""  # short phrase matching the FINAL action type; what other rows quote when
    # they compare themselves to this one, so a comparison can never contradict the row it cites

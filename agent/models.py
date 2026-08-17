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
    reserved: Optional[int] = None
    lead_time_days: Optional[int] = None


@dataclass(frozen=True)
class Conflict:
    sku: str
    type: str
    claims: list


def field_for(conflict):
    """Map conflict type to Record field name."""
    return "price" if conflict.type == "price" else "qty"


@dataclass(frozen=True)
class Action:
    sku: str
    type: str
    priority: float
    category: str
    reason: str
    confidence: float = 0.0
    summary: str = ""

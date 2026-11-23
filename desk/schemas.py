"""Challenge 3: the OrderRequest schema every extraction must validate against."""
import re
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Intent = Literal["order", "status", "complaint", "return", "other"]
Urgency = Literal["low", "normal", "high"]  # matches the dataset's hand labels


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Optional[str] = Field(None, description="Catalog SKU (e.g. MS-VLV0100) ONLY if written in the email, else null. Never guess.")
    description: str = Field(min_length=1, description="The product as the customer wrote it")
    quantity: int = Field(gt=0, description="Whole number of units")
    unit: str = Field(min_length=1, description="pcs for nos/pieces/units; otherwise singular as written: box, pair, drum, carboy, m")

    @field_validator("sku")
    @classmethod
    def sku_shape(cls, v):
        if v is not None and not re.fullmatch(r"MS-[A-Z]{3}\d{4}", v):
            raise ValueError("sku must look like MS-ABC1234 (copied exactly from the email) or be null")
        return v


class OrderRequest(BaseModel):
    """One customer email as data.

    Multi-intent rule: `intent` is the ONE primary thing the sender needs from Meridian; every other
    request in the same email goes in `secondary_intents` (no duplicates, never the primary again).
    """

    model_config = ConfigDict(extra="forbid")

    customer: str = Field(min_length=1, description="Sender's company name from the signature (person's name if no company)")
    intent: Intent = Field(description="Primary intent")
    secondary_intents: list[Intent] = Field(description="Other intents in the same email; [] if none")
    line_items: list[LineItem] = Field(description="Products named for ordering, returning or complaining about; [] if none")
    requested_date: Optional[str] = Field(description="YYYY-MM-DD resolved from the email's Date header; the original phrase if it cannot be resolved; null if none")
    urgency: Urgency
    confidence: float = Field(ge=0.0, le=1.0, description="Your confidence in this extraction, 0-1")

    @field_validator("requested_date")
    @classmethod
    def real_iso_date(cls, v):
        if v is not None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            date.fromisoformat(v)  # raises ValueError for e.g. 2026-02-30
        if v is not None and not v.strip():
            raise ValueError("requested_date must be null instead of an empty string")
        return v

    @model_validator(mode="after")
    def multi_intent_rule(self):
        if self.intent in self.secondary_intents:
            raise ValueError(f"secondary_intents must not repeat the primary intent {self.intent!r}")
        if len(set(self.secondary_intents)) != len(self.secondary_intents):
            raise ValueError("secondary_intents must not contain duplicates")
        return self


def response_schema():
    """JSON Schema sent to the model (Groq response_format json_schema)."""
    return OrderRequest.model_json_schema()

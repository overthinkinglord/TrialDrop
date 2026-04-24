from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class TrialDraft:
    service_name: Optional[str] = None
    service_key_normalized: Optional[str] = None
    amount_minor: Optional[int] = None
    currency_code: Optional[str] = None
    started_at: Optional[str] = None
    billing_at: Optional[str] = None
    raw_input: str = ""

    @property
    def is_complete(self) -> bool:
        return all(
            [
                self.service_name,
                self.service_key_normalized,
                self.amount_minor is not None,
                self.currency_code,
                self.started_at,
                self.billing_at,
            ]
        )

    def missing_fields(self) -> List[str]:
        missing: List[str] = []
        if self.amount_minor is None or not self.currency_code:
            missing.append("amount")
        if not self.billing_at:
            missing.append("date")
        return missing

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service_name": self.service_name,
            "service_key_normalized": self.service_key_normalized,
            "amount_minor": self.amount_minor,
            "currency_code": self.currency_code,
            "started_at": self.started_at,
            "billing_at": self.billing_at,
            "raw_input": self.raw_input,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "TrialDraft":
        return cls(
            service_name=payload.get("service_name"),
            service_key_normalized=payload.get("service_key_normalized"),
            amount_minor=payload.get("amount_minor"),
            currency_code=payload.get("currency_code"),
            started_at=payload.get("started_at"),
            billing_at=payload.get("billing_at"),
            raw_input=payload.get("raw_input", ""),
        )


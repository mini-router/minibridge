from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from .models import Usage


@dataclass(frozen=True)
class ModelPrice:
    model: str
    input_per_1k: Decimal
    output_per_1k: Decimal


@dataclass(frozen=True)
class PricingTable:
    pricing_table_id: str
    models: dict[str, ModelPrice]

    def get(self, model: str) -> ModelPrice:
        try:
            return self.models[model]
        except KeyError as exc:
            raise KeyError(f"no pricing entry for model {model!r}") from exc

    def compute_cost(self, model: str, usage: Usage) -> Decimal:
        price = self.get(model)
        input_cost = (Decimal(usage.input_tokens) / Decimal(1000)) * price.input_per_1k
        output_cost = (Decimal(usage.output_tokens) / Decimal(1000)) * price.output_per_1k
        return (input_cost + output_cost).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    def estimated_max_cost(self, model: str, input_tokens: int, max_output_tokens: int) -> Decimal:
        price = self.get(model)
        input_cost = (Decimal(input_tokens) / Decimal(1000)) * price.input_per_1k
        output_cost = (Decimal(max_output_tokens) / Decimal(1000)) * price.output_per_1k
        return (input_cost + output_cost).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

"""Closed aggregate evidence for measured reviewer requests, never price forecasts."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from mechanical_librarian.domain.reviewer_access import (
    DescriptionModelMetadata,
    OpenRouterModelId,
)

Count = Annotated[StrictInt, Field(ge=0, le=100_000)]
Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Price = Annotated[StrictStr, Field(min_length=1, max_length=40)]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class MeasuredRun(_Closed):
    source_head: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]
    cohort_sha256: Digest
    observed_at: Annotated[StrictInt, Field(ge=0)]


class ReviewerCostEstimate(_Closed):
    model: OpenRouterModelId
    task: Literal["cover", "description"]
    requests_selected: Annotated[StrictInt, Field(ge=1, le=100_000)]
    requests_attempted: Annotated[StrictInt, Field(ge=1, le=100_000)]
    requests_completed: Count
    requests_failed: Count
    works: Annotated[StrictInt, Field(ge=1, le=100_000)]
    distinct_images: Count
    total_cost_usd: Price
    per_1000_usd: Price

    @field_validator("total_cost_usd", "per_1000_usd")
    @classmethod
    def price(cls, value: str) -> str:
        return DescriptionModelMetadata.bounded_price(value)

    @model_validator(mode="after")
    def complete_accounting(self) -> ReviewerCostEstimate:
        if (
            self.requests_selected != self.requests_attempted
            or self.requests_completed + self.requests_failed != self.requests_attempted
            or self.works > self.requests_selected
            or (self.task == "description" and self.distinct_images != 0)
            or Decimal(self.per_1000_usd)
            != per_thousand(self.total_cost_usd, self.requests_attempted)
        ):
            raise ValueError("incomplete cost accounting")
        return self


def per_thousand(total: str, attempts: int) -> Decimal:
    # Fixed decimal arithmetic is shared by capture and public admission.
    with localcontext() as context:
        context.prec = 28
        return Decimal(total) / attempts * 1000


class ReviewerCostEvidence(_Closed):
    schema_version: Literal["reviewer-cost-evidence-v1"]
    measured_run: MeasuredRun | None
    models: Annotated[tuple[ReviewerCostEstimate, ...], Field(max_length=100)]

    @model_validator(mode="after")
    def unique_complete_rows(self) -> ReviewerCostEvidence:
        if bool(self.models) != (self.measured_run is not None) or len(
            {(row.model, row.task) for row in self.models}
        ) != len(self.models):
            raise ValueError("invalid measured evidence")
        return self

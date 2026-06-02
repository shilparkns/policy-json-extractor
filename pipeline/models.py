from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel
from pipeline.config import VERSION


class Variable(BaseModel):
    name: str
    unit: str | None = None


class Condition(BaseModel):
    logic: Literal["AND", "OR"] | None = None
    clauses: list[Condition] | None = None
    subject: str | None = None
    operator: Literal[">", "<", ">=", "<=", "==", "!=", "in", "between"] | None = None
    value: Any | None = None


class Constraint(BaseModel):
    subject: str
    operator: str
    value: Any
    unit: str | None = None


class Formula(BaseModel):
    expression: str
    result_unit: str | None = None


class Outcome(BaseModel):
    constraints: list[Constraint] = []
    formula: Formula | None = None


class Branch(BaseModel):
    condition: Condition | Literal["default"]
    outcome: Outcome


class Source(BaseModel):
    document_id: str
    section: str
    section_title: str
    clause: str
    raw_text: str


class Rule(BaseModel):
    id: str
    source: Source
    applies_to: Literal["applicant", "policy", "portfolio"]
    variables: list[Variable]
    branches: list[Branch]
    confidence: float
    tags: list[str]
    version: str = VERSION


class ExtractionMetadata(BaseModel):
    document_id: str
    mode: str
    model: str
    extracted_at: str
    total_rules: int
    flagged_rules: int
    average_confidence: float


class ExtractionOutput(BaseModel):
    extraction_metadata: ExtractionMetadata
    rules: list[Rule]

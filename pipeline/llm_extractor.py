import json
from openai import OpenAI
from dotenv import load_dotenv
from pipeline.config import MODEL

load_dotenv()

SCHEMA_AND_EXAMPLE = """
Return a JSON object with a single key "rules" containing an array of Rule objects: {"rules": [...]}
Each Rule must match this schema exactly:

{
  "id": "<document_id>_<section>_<clause>_<n>",      // n=1,2,... if multiple rules per clause
  "source": {
    "document_id": "<str>",
    "section": "<str>",
    "section_title": "<str>",
    "clause": "<str>",
    "raw_text": "<verbatim text of the clause — do not paraphrase>"
  },
  "applies_to": "applicant" | "policy" | "portfolio",
  "variables": [
    { "name": "<snake_case>", "unit": "<str>|null" }
  ],
  "branches": [
    {
      "condition": "default" | { "logic": "AND"|"OR"|null, "clauses": [...] | null, "subject": "<str>|null", "operator": ">"|"<"|">="|"<="|"=="|"!="|"in"|"between"|null, "value": <any>|null },
      "outcome": {
        "constraints": [ { "subject": "<str>", "operator": "<str>", "value": <any>, "unit": "<str>|null" } ],
        "formula": { "expression": "<str>", "result_unit": "<str>|null" } | null
      },
    }
  ],
  "confidence": 1.0,
  "tags": ["<str>", ...],
  "version": "1.0"
}

Rules:
- One Rule per logical rule in the clause. A clause encoding two independent rules = two Rule objects.
- A clause may have multiple constraints in one outcome. Use the constraints list for this.
- raw_text must be copied verbatim from the input. Never paraphrase.
- Use "default" (the string) as condition when there is no conditional logic.
- Use formula when the clause defines a computed amount or fee. Use constraints for threshold rules.
- applies_to: use "applicant" for individual eligibility rules, "policy" for policy-level rules and fees, "portfolio" for aggregate portfolio limits.
- units: always write "percent" not "%". Use standard unit names as they appear in the text.
- Numeric values: use the value exactly as written in the source text. Do not multiply or divide to convert units.
- Condition.clauses is only for nested Condition objects. For a simple condition, set clauses: null and use subject/operator/value directly.
- tags: short snake_case strings describing the subject of the rule.

Example 1 — constraint rule: "Each applicant must have a credit score of at least 680."

{"rules": [
  {
    "id": "sample1_5.1_a_1",
    "source": {
      "document_id": "sample1",
      "section": "5.1",
      "section_title": "Eligibility Criteria",
      "clause": "a",
      "raw_text": "Each applicant must have a credit score of at least 680."
    },
    "applies_to": "applicant",
    "variables": [
      { "name": "credit_score", "unit": null }
    ],
    "branches": [
      {
        "condition": "default",
        "outcome": {
          "constraints": [ { "subject": "credit_score", "operator": ">=", "value": 680, "unit": null } ],
          "formula": null
        },
      }
    ],
    "confidence": 1.0,
    "tags": ["credit_score", "eligibility"],
    "version": "1.0"
  }
]}

Example 2 — computed formula: "Annual Service Fee: 0.35% per annum of the outstanding coverage amount, payable monthly."

{"rules": [
  {
    "id": "sample3_12.2_b_1",
    "source": {
      "document_id": "sample3",
      "section": "12.2",
      "section_title": "Fees And Charges",
      "clause": "b",
      "raw_text": "Annual Service Fee: 0.35% per annum of the outstanding coverage amount, payable monthly."
    },
    "applies_to": "policy",
    "variables": [
      { "name": "outstanding_coverage_amount", "unit": "USD" },
      { "name": "annual_service_fee", "unit": "USD" }
    ],
    "branches": [
      {
        "condition": "default",
        "outcome": {
          "constraints": [],
          "formula": { "expression": "annual_service_fee = 0.35 * outstanding_coverage_amount / 100", "result_unit": "USD" }
        },
      }
    ],
    "confidence": 1.0,
    "tags": ["fee", "annual_service"],
    "version": "1.0"
  }
]}

Example 3 — two-branch conditional rule: "The debt-to-income ratio shall not exceed 40%, or 45% if the applicant has a co-signer with a credit score above 750."

{"rules": [
  {
    "id": "sample1_5.1_d_1",
    "source": {
      "document_id": "sample1",
      "section": "5.1",
      "section_title": "Eligibility Criteria",
      "clause": "d",
      "raw_text": "The debt-to-income ratio shall not exceed 40%, or 45% if the applicant has a co-signer with a credit score above 750."
    },
    "applies_to": "applicant",
    "variables": [
      { "name": "debt_to_income_ratio", "unit": "percent" },
      { "name": "cosigner_credit_score", "unit": null }
    ],
    "branches": [
      {
        "condition": { "logic": null, "clauses": null, "subject": "cosigner_credit_score", "operator": ">", "value": 750 },
        "outcome": {
          "constraints": [ { "subject": "debt_to_income_ratio", "operator": "<=", "value": 45, "unit": "percent" } ],
          "formula": null
        },
      },
      {
        "condition": "default",
        "outcome": {
          "constraints": [ { "subject": "debt_to_income_ratio", "operator": "<=", "value": 40, "unit": "percent" } ],
          "formula": null
        },
      }
    ],
    "confidence": 1.0,
    "tags": ["dti", "co_signer", "eligibility"],
    "version": "1.0"
  }
]}
"""

STRICT_ADDITION = """
IMPORTANT: Your previous response failed validation. Return ONLY a JSON object {"rules": [...]}.
No markdown fences, no commentary, no trailing text.
"""


def _build_system_prompt(strict: bool) -> str:
    # strict=True appends extra instructions after a schema validation failure
    base = "You extract structured policy rules from insurance/legal document clauses.\n\nReturn ONLY valid JSON. No markdown, no preamble, no explanation.\n"
    base += SCHEMA_AND_EXAMPLE
    if strict:
        base += STRICT_ADDITION
    return base


def _call_llm(system: str, user: str) -> str:
    client = OpenAI()
    response = client.chat.completions.create(
        model=MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    # Unwrap {"rules": [...]} → "[...]" so the validator receives a JSON array
    content = response.choices[0].message.content
    data = json.loads(content)
    return json.dumps(data.get("rules", data))


def _clause_user_prompt(clause: dict, document_id: str) -> str:
    lines = [f"document_id: {document_id}"]
    lines.append(f"section: {clause['section']} - {clause['section_title']}")
    if clause.get("section_context"):
        lines.append(f"section_context: {clause['section_context']}")
    lines.append(f"clause: ({clause['clause']}) {clause['raw_text']}")
    return "\n".join(lines)


def _section_user_prompt(clauses: list[dict], document_id: str) -> str:
    first = clauses[0]
    lines = [f"document_id: {document_id}"]
    lines.append(f"section: {first['section']} - {first['section_title']}")
    if first.get("section_context"):
        lines.append(f"section_context: {first['section_context']}")
    lines.append("")
    for c in clauses:
        lines.append(f"({c['clause']}) {c['raw_text']}")
    return "\n".join(lines)


def extract_clause(clause: dict, document_id: str, strict: bool = False) -> str:
    """Extract rules from a single clause. Returns raw LLM response string."""
    label = f"{clause['section']}({clause['clause']})"
    print(f"[llm_extractor] Extracting clause {label}" + (" [strict retry]" if strict else ""))
    system = _build_system_prompt(strict)
    user = _clause_user_prompt(clause, document_id)
    return _call_llm(system, user)


def extract_section(clauses: list[dict], document_id: str, strict: bool = False) -> str:
    """Extract rules from all clauses in a section. Returns raw LLM response string."""
    section = clauses[0]["section"]
    print(f"[llm_extractor] Extracting section {section} ({len(clauses)} clauses)" + (" [strict retry]" if strict else ""))
    system = _build_system_prompt(strict)
    user = _section_user_prompt(clauses, document_id)
    return _call_llm(system, user)

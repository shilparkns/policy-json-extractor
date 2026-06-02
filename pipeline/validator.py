import json
import re
from pipeline.models import Rule
from pipeline.config import CONFIDENCE_THRESHOLDS


def validate(raw_response: str, retry_fn) -> list[Rule]:
    """
    Run three checks on raw LLM output. Returns validated Rules with confidence set.
    retry_fn: callable() -> str  — called once if schema check fails.
    """
    # Check 1: JSON parsing — retry once if malformed
    data = _parse_json(raw_response)
    if data is None:
        print("[validator] WARNING: JSON parse failed — retrying with strict prompt")
        data = _parse_json(retry_fn())
        if data is None:
            return []

    # Check 2: Schema compliance
    rules = _parse_schema(data)
    if rules is None:
        print("[validator] WARNING: schema check failed — retrying with strict prompt")
        raw_retry = retry_fn()
        data = _parse_json(raw_retry)
        if data is None:
            return []
        rules = _parse_schema(data)
        if rules is None:
            print("[validator] ERROR: schema check failed after retry — flagging rules")
            return _build_flagged(data, CONFIDENCE_THRESHOLDS["schema_fail"])

    # Normalize clause field — strip parentheses the LLM may have added
    for rule in rules:
        rule.source.clause = rule.source.clause.strip("()")
        # Normalize unit representation — "%" and "percent" are the same
        for branch in rule.branches:
            for c in branch.outcome.constraints:
                if c.unit == "%":
                    c.unit = "percent"

    # Check 3: Hallucination check
    rules = _check_hallucination(rules)

    return rules


def _parse_json(raw: str) -> list | None:
    """Return parsed list if valid JSON, else log and return None."""
    try:
        data = json.loads(raw.strip())
        if not isinstance(data, list):
            print(f"[validator] ERROR: expected JSON array, got {type(data).__name__}")
            return None
        return data
    except json.JSONDecodeError as e:
        print(f"[validator] ERROR: JSON parse failed — {e} — confidence {CONFIDENCE_THRESHOLDS['json_parse_fail']}")
        return None


def _parse_schema(data: list) -> list[Rule] | None:
    """Validate each item against Pydantic Rule model. Returns None if any item fails."""
    rules = []
    for item in data:
        try:
            rules.append(Rule.model_validate(item))
        except Exception as e:
            print(f"[validator] ERROR: schema validation failed — {e}")
            return None
    return rules


def _check_hallucination(rules: list[Rule]) -> list[Rule]:
    """Check that all numeric values in each rule appear in its raw_text."""
    checked = []
    for rule in rules:
        nums = _extract_numbers_from_rule(rule)
        raw = rule.source.raw_text
        missing = [n for n in nums if not _number_in_text(n, raw)]
        if missing:
            print(
                f"[validator] WARNING: hallucination flag on {rule.source.section}({rule.source.clause}) "
                f"— values not in raw_text: {missing} — confidence {CONFIDENCE_THRESHOLDS['hallucination_flag']}"
            )
            rule.confidence = CONFIDENCE_THRESHOLDS["hallucination_flag"]
        else:
            rule.confidence = CONFIDENCE_THRESHOLDS["all_pass"]
        checked.append(rule)
    return checked


def _extract_numbers_from_rule(rule: Rule) -> list[str]:
    """Pull numeric values from constraints only — formulas contain derived arithmetic constants."""
    numbers = []
    for branch in rule.branches:
        if isinstance(branch.condition, object) and hasattr(branch.condition, 'value'):
            if branch.condition.value is not None:
                numbers.extend(re.findall(r"\b\d+(?:\.\d+)?\b", str(branch.condition.value)))
        for c in branch.outcome.constraints:
            numbers.extend(re.findall(r"\b\d+(?:\.\d+)?\b", json.dumps(c.model_dump())))
    return numbers


def _number_in_text(num: str, text: str) -> bool:
    """Check whether a numeric string appears in text (ignoring formatting like $ and %)."""
    # Strip commas so "35,000" matches extracted value "35000"
    clean = text.replace(",", "")
    if re.search(rf"\b{re.escape(num)}\b", clean):
        return True
    # If value is a decimal < 1, also accept its percentage equivalent (0.15 → 15)
    # This handles the LLM converting "15%" to 0.15 in constraint values
    try:
        f = float(num)
        if 0 < f < 1:
            pct = str(int(f * 100)) if (f * 100).is_integer() else str(round(f * 100, 10))
            if re.search(rf"\b{re.escape(pct)}\b", clean):
                return True
    except ValueError:
        pass
    return False


def _build_flagged(data: list, confidence: float) -> list[Rule]:
    """Best-effort Rule construction for items that survived JSON parse but failed schema."""
    rules = []
    for item in data:
        try:
            rule = Rule.model_validate(item)
            rule.confidence = confidence
            rules.append(rule)
        except Exception:
            pass  # truly unrecoverable — skip silently, already logged above
    return rules

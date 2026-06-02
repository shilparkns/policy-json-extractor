# Policy Rule Extraction Pipeline

Extracts structured, machine-readable rules from insurance policy document text using a hybrid regex + LLM approach.

---

## How to Run

**Requirements:** Python 3.11+, an OpenAI API key.

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set OPENAI_API_KEY=...
```

**Extract:**
```bash
python extract.py --input-dir samples/ --mode clause   # all samples
python extract.py --input samples/sample1.txt --mode clause  # single file
```

Output is written to `output/<document_id>_<mode>.json`.

**Evaluate:**
```bash
python evaluate.py --predicted output/ --mode clause          # all samples
python evaluate.py --predicted output/sample1_clause.json     # single file
python evaluate.py --predicted output/ --mode clause --detail # with rule-by-rule breakdown
```

---

## Approach and Design Decisions

### Use regex for structure, LLM for semantics — never the reverse.

Regex detects section headers, extracts preambles, and splits clauses by markers like `(a)`, `(b)`, `(i)`. This is deterministic, fast, and correct — policy documents have consistent enough structure for it. The LLM only sees the clause text, which is what it's actually good at: interpreting conditional logic, identifying variables, and mapping semantics to a typed schema.

A pure LLM approach would make document segmentation unreliable. A pure regex approach fails immediately on anything conditional — `5.1(d)` requires understanding that "or 45% if..." is a branch, not a separate rule.

### Clause mode for accuracy, section mode for production.

Clause mode (one API call per clause) produces more precise output — the LLM has a narrow input and errors are contained. Section mode (one API call per section) uses 7x fewer API calls and gives the LLM full context for clauses that reference a shared preamble. Clause mode performed better on evaluation; section mode is the right default at scale.

### Rules are branches, not flat constraints.

A rule with a baseline and an exception is one Rule object with two branches, not two separate rules. `5.1(d)` is one rule; `5.1(g)` is one rule. Only clauses encoding genuinely independent constraints — like `5.1(c)` — are split into multiple Rule objects. This keeps the schema clean and avoids artificial rule proliferation.

Outcomes use `constraints: list[Constraint]` rather than a singular constraint, so a rule like `7.3(ii)` can express both a filter and a portfolio limit in one outcome. Fees use `formula` — a computed expression is more faithful than forcing a fee into a threshold constraint.

### What we deliberately left out.

**Enforcement levels** (`hard_block` / `soft_flag` / `advisory`) were removed from the schema. They're not derivable from document text — "shall not exceed" appears in both hard eligibility rules and soft portfolio guidelines. Enforcement belongs in the business logic layer, not the extraction layer.

**An expression language for rule evaluation** was never considered. Without a known rule engine downstream, building an evaluatable expression format would be over-engineering against a requirement that doesn't exist yet.

### Validation runs three checks on every LLM response.

1. **JSON parse** — OpenAI's `json_object` mode eliminates most syntax errors. On failure, one retry with a stricter prompt.
2. **Schema compliance** — Pydantic validates every field. One retry on failure; flagged at confidence 0.4 if it still fails.
3. **Hallucination check** — Every numeric value in constraints is verified against the source clause text. Values not found in the text are flagged at confidence 0.6. Percentage-to-decimal equivalents are accepted.

---

## What the LLM Does Well and Where It Struggles

Evaluated against ground truth on all three samples (clause mode). Field accuracy: 90%, F1: 97%.

### Does well

**Conditional branching.** `5.1(d)` — "DTI shall not exceed 40%, or 45% if the applicant has a co-signer with a credit score above 750" — is correctly extracted as a two-branch rule every time. The LLM separates the conditional branch (co-signer > 750 → DTI ≤ 45%) from the default (DTI ≤ 40%) without explicit instruction.

**Conditional exceptions.** `5.1(g)` — "unless the applicant is enrolled in an approved assistance program, in which case the minimum income shall be $25,000" — correctly produces two branches with the right values on each.

**Fee rules.** All five fee clauses in section 12.2 are correctly extracted. `12.2(c)` has a conditional trigger, a computed fee, and explicit min/max bounds — all captured in a single rule.

**Portfolio limits.** Four of five concentration rules in section 7.3 are correct, with right `applies_to: portfolio` classification, operators, and values.

### Struggles

**Dual-dimension rules.** `7.3(ii)` — "No more than 10% of the total portfolio value shall consist of policies with a coverage amount exceeding $1,500,000" — encodes a filter ($1.5M threshold) and a portfolio limit (10%). The LLM consistently extracts the filter instead of the concentration limit. The rule can't be fully represented as a single constraint without losing information, and the LLM picks the wrong dimension.

**Operator direction on prohibitions.** `5.1(e)` — "must not have any payment more than 30 days overdue" — the LLM extracts `days_overdue > 30` (the prohibited condition) instead of `days_overdue <= 30` (the required constraint). Clauses phrased as prohibitions confuse the LLM on which side of the threshold to use.

**Value completeness.** `5.1(f)` — the LLM drops "US" from "US territories", producing `['United States', 'territories']`. Minor but incorrect.

**Rule splitting.** `5.1(c)` encodes two independent rules. The LLM captures both constraints but combines them into one Rule object — the portfolio concentration limit is missed as a standalone rule.

---

## How You Would Evaluate Accuracy at 500 Documents

The core problem is ground truth. With 3 samples you write it by hand. With 500 documents you can't — the solution is a three-layer approach that builds ground truth incrementally.

**Layer 1 — Automated evaluation on all 500 documents.** `evaluate.py` runs on everything. Catches schema failures, hallucinations, missing rules, field mismatches. Free, but only as good as the ground truth you have.

**Layer 2 — LLM-as-judge on a sample of ~50 documents.** A judge LLM receives the original clause, the extracted rule, and a rubric. Catches what the programmatic evaluator misses: formula equivalence, `applies_to` inference quality, branching correctness. Cost is roughly double the extraction cost on the sample.

**Layer 3 — Human annotation on a stratified sample of ~20-30 documents.** Annotate strategically — not randomly. Prioritize: documents where the judge flagged low confidence, documents with complex conditional logic, one from each policy type seen. Two annotators per document; disagreements signal genuine ambiguity, which is a prompt or schema problem. This set becomes the gold standard for regression testing on every prompt change.

**The feedback loop is what makes it scale.** Human corrections feed back into ground truth, which improves automated evaluation on the next run. After six months of production traffic, ground truth grows organically from real corrections without a dedicated annotation effort.

**The most important metric is confidence calibration, not F1.** A pipeline that knows when it's wrong is more operationally valuable than one that's slightly more accurate but doesn't know its own failure modes. If rules at 0.95 confidence are only correct 70% of the time, the validator thresholds are broken and the human review queue is missing real errors.

---

## What You Would Change for a Production System

### Architecture and scaling

The biggest structural change is replacing the CLI script with an event-driven pipeline. Document uploaded → extraction job triggered → worker runs the pipeline → results published to an output queue → downstream consumers subscribe. No polling. Failed jobs go to a dead letter queue with exponential backoff retry. This decouples ingestion from extraction from consumption — each can scale independently.

For LLM call volume, switch to section mode as the default — 7x fewer API calls than clause mode with equivalent quality. For sections with 20+ clauses, batch into groups of 10, repeating the section header and preamble on each batch so the LLM always has context. At 500 documents × 10 sections average, that's roughly 5,000 API calls vs 35,000 in clause mode — a meaningful cost difference at scale.

### Data and storage

Plain JSON is fine for a pilot. At scale, start with compressed JSON — 60-70% size reduction with zero schema changes. Move to Parquet if analytics queries over constraint values become a bottleneck; columnar storage is significantly faster for that access pattern.

Two integrity concerns worth handling early: schema versioning and raw_text hashing. Every extracted rule should store the schema version it was produced with — when the schema evolves, old rules remain readable against their version rather than silently breaking downstream consumers. And `raw_text` should be hashed at extraction time and verified at consumption time — audit and explainability tools depend entirely on verbatim text, and silent modification breaks them in ways that are hard to detect.

### Observability and failure modes

Emit structured events at each pipeline stage: document received, section parsed, extraction started, extraction completed, extraction failed, validation flagged. The most operationally valuable event is `review.completed` — every human correction is simultaneously a prompt improvement signal and a confidence threshold calibration data point.

The two most likely downstream failure points are `source_hint` field name mismatches between the extractor and the consuming system's data model — fix with a field mapping registry — and tag inconsistency making tag-based queries return incomplete results — fix by enforcing a controlled vocabulary at validation time and rejecting unknown tags. A third subtler failure: confidence miscalibration silently flooding or starving the human review queue. Track what percentage of rules at each confidence band are actually correct — if rules at 0.95 confidence are only correct 70% of the time, the validator thresholds are wrong and real errors are slipping through.

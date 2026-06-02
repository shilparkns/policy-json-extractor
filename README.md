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

### Use regex for structure, LLM for semantics.

Regex detects section headers, extracts preambles, and splits clauses by markers like `(a)`, `(b)`, `(i)`. This is deterministic, fast, and correct — policy documents have consistent enough structure for it. The LLM only sees the clause text, which is what it's actually good at: interpreting conditional logic, identifying variables, and mapping semantics to a typed schema.

A pure LLM approach would make document segmentation unreliable. A pure regex approach fails immediately on anything conditional — `5.1(d)` requires understanding that "or 45% if..." is a branch, not a separate rule.

### Clause mode for accuracy, section mode for production.

Clause mode (one API call per clause) produces more precise output — the LLM has a narrow input and errors are contained. Section mode (one API call per section) uses 7x fewer API calls and gives the LLM full context for clauses that reference a shared preamble. Clause mode performed better on evaluation; section mode is the right default at scale.

### Rules are branches, not flat constraints.

A rule with a baseline and an exception is one Rule object with two branches, not two separate rules. `5.1(d)` is one rule; `5.1(g)` is one rule. Only clauses encoding genuinely independent constraints — like `5.1(c)` — are split into multiple Rule objects. This keeps the schema clean and avoids artificial rule proliferation.

Outcomes use `constraints: list[Constraint]` rather than a singular constraint, so a rule like `7.3(ii)` can express both a filter and a portfolio limit in one outcome. Fees use `formula` — a computed expression is more faithful than forcing a fee into a threshold constraint.

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

**Dual-dimension rules.** 7.3(ii) has two numbers that matter: 10% (the portfolio limit) and $1,500,000 (the filter defining which policies count). The LLM picks one and loses the other. This is a fundamental limitation — a single constraint can only hold one value, which is why we moved to constraints: list.

**Prohibition phrasing.** 5.1(e) says "must not have any payment more than 30 days overdue." The LLM extracts the prohibited condition (days_overdue > 30) instead of the passing threshold (days_overdue <= 30). When a clause is written as a prohibition rather than a requirement, the LLM gets the operator direction wrong.

**Incomplete list values.** 5.1(f) says "United States or its territories." The LLM produces ['United States', 'territories'] instead of ['United States', 'US territories']. Small but wrong.

**Rule splitting.** 5.1(c) is genuinely two rules — a per-applicant credit score threshold and a portfolio concentration limit — written as one clause. The LLM sees one clause and outputs one rule, missing the second.

---

## How You Would Evaluate Accuracy at 500 Documents

With 3 samples you write ground truth by hand. With 500 you can't — you need a systematic approach where ground truth grows as a byproduct of the evaluation process itself.

**Layer 1 — Automated validation on all 500 documents.** Checks what doesn't require ground truth: schema compliance, numeric hallucinations, JSON validity, rule count per clause. For documents where ground truth exists, also runs field-level accuracy and precision/recall. Free and fast, but correctness measurement is only possible where ground truth has been built.

**Layer 2 — LLM-as-judge on a sample of ~50 documents.** A judge LLM receives the original clause, the extracted rule, and a rubric. Catches what the programmatic evaluator misses: formula equivalence, applies_to inference quality, branching correctness. Cost is roughly double the extraction cost on the sample.

**Layer 3 — Human annotation on a stratified sample of ~20-30 documents.** This is where ground truth gets created. Run the extractor first and use the output as a draft — reviewers correct rather than annotate from scratch. Two reviewers per document; where they agree that becomes ground truth, where they disagree the clause is genuinely ambiguous and signals a schema or prompt problem. Prioritize documents where Layer 2 flagged low confidence, complex conditional clauses, and one document per policy type seen. This set becomes the gold standard for regression testing on every prompt change, and grows organically as corrections accumulate from production traffic.

The most important metric is confidence calibration, not F1. A pipeline that knows when it's wrong is more operationally valuable than one that's slightly more accurate but doesn't know its own failure modes. If rules at 0.95 confidence are only correct 70% of the time, the validator thresholds are broken and real errors are slipping through the human review queue undetected.

---

## What You Would Change for a Production System

**Document ingestion**
Right now the pipeline takes plain text. In production, documents arrive as PDFs, scanned images, and Word files. You need an ingestion layer that handles format conversion and text extraction before the pipeline starts — including reading text from scanned document images. This is where most production pipelines fail first — not in the extraction step.

**Event-driven architecture**
Replace the CLI script with a job queue. Document uploaded → extraction job triggered → worker runs the pipeline → results published to an output queue → downstream consumers subscribe. No polling. Failed jobs go to a dead letter queue with exponential backoff retry. This decouples ingestion from extraction from consumption — each can scale independently.

**LLM call volume**
Switch to section mode as the default — 7x fewer API calls than clause mode with equivalent quality. For sections with 20+ clauses, batch into groups of 10, repeating the section header and preamble on each batch. At 500 documents × 10 sections average, that's roughly 5,000 API calls vs 35,000 in clause mode. When the LLM API goes down, you need circuit breakers, fallback behavior, and rate limit management — not just retry logic.

**Data and storage**
Plain JSON is fine for a pilot. At scale, start with compressed JSON — 60-70% size reduction with zero schema changes. Move to Parquet if analytics queries over constraint values become a bottleneck.

**Schema evolution**
Every extracted rule stores the schema version it was produced with — when the schema evolves, old rules remain readable against their version rather than silently breaking downstream consumers. raw_text is hashed at extraction time and verified at consumption time — audit tools depend entirely on verbatim text and silent modification breaks them in ways that are hard to detect.

**Data drift**
Policy document formats change over time. Your regex parser breaks silently when section headers change format. Your prompt stops working when new rule types appear that your examples don't cover. You need monitoring that detects when extraction quality degrades — not just whether the pipeline runs, but whether what it produces is still correct.

**Human review loop**
Low confidence rules go to a review queue. You need a review interface, a time limit on how long a rule can sit unreviewed before downstream systems are blocked, and a way to handle rules stuck in review that consumers are waiting on. Every correction feeds back as a prompt improvement signal and confidence threshold calibration data point.

**Observability**
Emit structured events at each pipeline stage: document received, section parsed, extraction started, extraction completed, extraction failed, validation flagged. The most valuable event is review.completed — it closes the feedback loop between human corrections and pipeline improvement.

**Downstream failure modes**
Two failure points worth handling early. Tag inconsistency making tag-based queries return incomplete results — fix by enforcing a controlled vocabulary at validation time. Confidence miscalibration silently flooding or starving the human review queue — track what percentage of rules at each confidence band are actually correct and recalibrate thresholds when they drift.

---

## What Was Deliberately Excluded

**Enforcement level** — hard block vs soft flag — was excluded from the schema entirely. Every clause uses mandatory language regardless of whether it's an eligibility rule or a concentration limit; enforcement is a business logic decision that belongs in the rule engine, not the extractor. 

**A compiled expression language for rule evaluation** was considered and rejected — over-engineered without a known downstream rule engine to design for. The schema is an interchange format. Execution is someone else's problem until we know whose.

---

## JSON Schema

```
Rule
├── id                         # "document_id:section:clause"
├── source
│   ├── document_id
│   ├── section               # "5.1", "7.3"
│   ├── section_title         # "Eligibility Criteria"
│   ├── clause                # "e", "ii", "d"
│   └── raw_text              # verbatim original
├── applies_to                # applicant | policy | portfolio
├── variables []
│   └── Variable
│       ├── name              # "debt_to_income_ratio"
│       └── unit              # "percent", "USD", "days", null
├── branches []               # ordered; first match wins
│   └── Branch
│       ├── condition         # Condition | "default"
│       └── outcome
├── confidence                # float [0–1]
├── tags []                   # "eligibility", "fee", "concentration"
└── version                   # schema version

Condition                      # recursive tree for complex logic
├── logic                      # AND | OR | null
├── clauses []                 # recursive list of Condition | null
├── subject                    # variable being tested
├── operator                   # > | < | >= | <= | == | != | in | between
└── value                      # number | string | array

Outcome
├── constraints []             # multiple constraints for dual-dimension rules
│   └── Constraint
│       ├── subject
│       ├── operator
│       ├── value
│       └── unit
└── formula                    # null for non-fee rules
    ├── expression             # "base_fee + (loan_amount * 0.01)"
    └── result_unit
```

MODE = "clause"  # "clause" | "section"
MODEL = "gpt-4o-mini"
MAX_RETRIES = 1
CONFIDENCE_THRESHOLDS = {
    "json_parse_fail": 0.3,
    "schema_fail": 0.4,
    "hallucination_flag": 0.6,
    "all_pass": 0.95,
}
VERSION = "1.0"

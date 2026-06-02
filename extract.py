import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline.config import CONFIDENCE_THRESHOLDS, MODEL, MODE
from pipeline.llm_extractor import extract_clause, extract_section
from pipeline.models import ExtractionMetadata, ExtractionOutput
from pipeline.parser import parse_document
from pipeline.pre_processor import clean
from pipeline.validator import validate


def main():
    args = _parse_args()
    mode = args.mode or MODE

    if args.input_dir:
        input_files = sorted(Path(args.input_dir).glob("*.txt"))
        if not input_files:
            print(f"[extract] ERROR: no .txt files found in {args.input_dir}")
            sys.exit(1)
        print(f"[extract] Processing {len(input_files)} files from {args.input_dir}")
        for input_path in input_files:
            _process_file(input_path, mode, Path(args.output))
    else:
        if not args.input:
            print("[extract] ERROR: provide --input <file> or --input-dir <directory>")
            sys.exit(1)
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"[extract] ERROR: input file not found — {args.input}")
            sys.exit(1)
        _process_file(input_path, mode, Path(args.output))


def _process_file(input_path: Path, mode: str, output_dir: Path):
    document_id = input_path.stem  # filename without extension, e.g. "sample1"
    print(f"[extract] Starting — document: {document_id}, mode: {mode}")

    text = input_path.read_text(encoding="utf-8")
    text = clean(text)
    clauses = parse_document(text)

    all_rules = []
    if mode == "clause":
        all_rules = _run_clause_mode(clauses, document_id)
    elif mode == "section":
        all_rules = _run_section_mode(clauses, document_id)
    else:
        print(f"[extract] ERROR: unknown mode '{mode}' — use 'clause' or 'section'")
        sys.exit(1)

    flagged = [r for r in all_rules if r.confidence < CONFIDENCE_THRESHOLDS["all_pass"]]
    avg_confidence = round(sum(r.confidence for r in all_rules) / len(all_rules), 4) if all_rules else 0.0

    output = ExtractionOutput(
        extraction_metadata=ExtractionMetadata(
            document_id=document_id,
            mode=mode,
            model=MODEL,
            extracted_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            total_rules=len(all_rules),
            flagged_rules=len(flagged),
            average_confidence=avg_confidence,
        ),
        rules=all_rules,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{document_id}_{mode}.json"
    output_path.write_text(
        json.dumps(output.model_dump(), indent=2),
        encoding="utf-8",
    )

    print(f"[extract] Done — {len(all_rules)} rules, {len(flagged)} flagged, avg confidence {avg_confidence}")
    print(f"[extract] Output written to {output_path}")


def _run_clause_mode(clauses: list[dict], document_id: str) -> list:
    all_rules = []
    for clause in clauses:
        raw = extract_clause(clause, document_id)
        retry_fn = lambda c=clause: extract_clause(c, document_id, strict=True)  # c=clause captures loop var by value
        rules = validate(raw, retry_fn)
        if not rules:
            print(f"[extract] WARNING: clause {clause['section']}({clause['clause']}) produced 0 rules — skipping")
        all_rules.extend(rules)
    return all_rules


def _run_section_mode(clauses: list[dict], document_id: str) -> list:
    raw = extract_section(clauses, document_id)
    retry_fn = lambda: extract_section(clauses, document_id, strict=True)
    return validate(raw, retry_fn)


def _parse_args():
    parser = argparse.ArgumentParser(description="Extract structured rules from policy documents.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--input", help="Path to a single input .txt file")
    group.add_argument("--input-dir", help="Path to a directory — processes all .txt files inside")
    parser.add_argument("--mode", choices=["clause", "section"], default=None, help="Extraction mode (default: from config.py)")
    parser.add_argument("--output", default="output/", help="Output directory (default: output/)")
    return parser.parse_args()


if __name__ == "__main__":
    main()

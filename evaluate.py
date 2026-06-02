import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def main():
    args = _parse_args()
    gt_rules = _load_rules("output/ground_truth.json", "ground truth")
    gt_index = _index_by_clause(gt_rules)

    predicted_path = Path(args.predicted)
    all_matched, all_missing, all_extra = [], [], []

    if predicted_path.is_dir():
        pred_files = sorted(
            f for f in predicted_path.glob(f"*_{args.mode}.json")
            if f.name != "ground_truth.json"
        )
        if not pred_files:
            print(f"ERROR: no *_{args.mode}.json files found in {args.predicted}")
            sys.exit(1)
        per_doc, all_matched, all_missing, all_extra = _run_aggregate(gt_index, pred_files)
        n_gt_total = sum(len(v) for v in gt_index.values())
        n_pred_total = sum(m["n_pred"] for m, _ in per_doc.values())
        agg_metrics = _compute_metrics(all_matched, n_gt_total, n_pred_total)
        agg_fields = _avg_field_scores(per_doc)
        _print_aggregate_report(per_doc, agg_metrics, agg_fields, all_missing, all_extra)
    else:
        pred_rules = _load_rules(args.predicted, "predicted")
        matched, missing, extra = _match_rules(gt_index, _index_by_clause(pred_rules))
        metrics = _compute_metrics(matched, len(gt_rules), len(pred_rules))
        field_scores = _compute_field_scores(matched)
        _print_single_report(metrics, field_scores, missing, extra, len(gt_rules), len(pred_rules))
        all_matched, all_missing, all_extra = matched, missing, extra

    if args.detail:
        out_path = Path(args.output)
        _write_detail_report(all_matched, all_missing, all_extra, out_path)
        print(f"[evaluate] Detail report written to {out_path}")


# --- Loading ---

def _load_rules(path, label):
    try:
        data = json.load(open(path))
    except FileNotFoundError:
        print(f"ERROR: {label} file not found: {path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: {label} file is not valid JSON — {e}")
        sys.exit(1)
    if "rules" not in data:
        print(f"ERROR: {label} file has no 'rules' key")
        sys.exit(1)
    return data["rules"]


def _index_by_clause(rules):
    # Group by (section, clause) — one clause can produce multiple rules
    index = defaultdict(list)
    for rule in rules:
        key = (rule["source"]["section"], rule["source"]["clause"])
        index[key].append(rule)
    return index


# --- Matching ---

def _match_rules(gt_index, pred_index):
    matched, missing = [], []
    for key, gt_group in gt_index.items():
        pred_group = pred_index.get(key, [])
        for i, gt_rule in enumerate(gt_group):
            if i < len(pred_group):
                matched.append((gt_rule, pred_group[i]))
            else:
                missing.append(gt_rule)

    # Extra: pred rules beyond what GT expects at this clause
    extra = []
    for key, pred_group in pred_index.items():
        gt_count = len(gt_index.get(key, []))
        extra.extend(pred_group[gt_count:])

    return matched, missing, extra


# --- Scoring ---

EXACT_FIELDS = ["applies_to"]
BRANCH_FIELDS = ["condition.operator", "constraint.operator", "constraint.value", "constraint.unit"]


def _branch_values(rule, field):
    # Collect a set of values for a branch field across all branches
    values = set()
    for branch in rule.get("branches", []):
        if field == "condition.operator":
            cond = branch.get("condition")
            if isinstance(cond, dict) and cond.get("operator"):
                values.add(str(cond["operator"]))
        else:
            # constraints is now a list — collect from all constraints in this branch
            key = field.split(".")[-1]
            for c in (branch.get("outcome") or {}).get("constraints") or []:
                v = c.get(key)
                if v is not None:
                    values.add(str(v))
    return values


def _tag_score(gt_tags, pred_tags):
    # Soft match: exact hit = 1.0, substring overlap = 0.5, no match = 0.0
    # Scored per GT tag then averaged — measures how well GT is covered by PRED
    if not gt_tags and not pred_tags:
        return 1.0
    if not gt_tags or not pred_tags:
        return 0.0
    total = 0.0
    for gt in gt_tags:
        if gt in pred_tags:
            total += 1.0
        elif any(gt in p or p in gt for p in pred_tags):
            total += 0.5
    return total / len(gt_tags)


def _score_pair(gt, pred):
    scores = {}
    for f in EXACT_FIELDS:
        scores[f] = 1.0 if gt.get(f) == pred.get(f) else 0.0
    for f in BRANCH_FIELDS:
        gt_vals, pred_vals = _branch_values(gt, f), _branch_values(pred, f)
        if not gt_vals and not pred_vals:
            scores[f] = 1.0
        elif not gt_vals or not pred_vals:
            scores[f] = 0.0
        else:
            scores[f] = 1.0 if gt_vals == pred_vals else 0.0
    return scores


def _diff_pair(gt, pred):
    """Return list of (field, gt_val, pred_val) for every field that differs."""
    diffs = []
    for f in EXACT_FIELDS:
        if gt.get(f) != pred.get(f):
            diffs.append((f, gt.get(f), pred.get(f)))
    for f in BRANCH_FIELDS:
        gv, pv = _branch_values(gt, f), _branch_values(pred, f)
        if gv != pv:
            diffs.append((f, gv or "—", pv or "—"))
    return diffs


def _compute_metrics(matched, n_gt, n_pred):
    n = len(matched)
    precision = n / n_pred if n_pred else 0.0
    recall = n / n_gt if n_gt else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"n_matched": n, "n_gt": n_gt, "n_pred": n_pred,
            "precision": precision, "recall": recall, "f1": f1}


def _compute_field_scores(matched):
    totals = defaultdict(list)
    for gt, pred in matched:
        for field, score in _score_pair(gt, pred).items():
            totals[field].append(score)
    return {f: sum(v) / len(v) for f, v in totals.items()}


def _field_avg(field_scores):
    return sum(field_scores.values()) / len(field_scores) if field_scores else 0.0


def _avg_field_scores(per_doc):
    totals = defaultdict(list)
    for _, (_, fs) in per_doc.items():
        for f, s in fs.items():
            totals[f].append(s)
    return {f: sum(v) / len(v) for f, v in totals.items()}


# --- Aggregate mode ---

def _run_aggregate(gt_index, pred_files):
    per_doc = {}
    all_matched, all_missing, all_extra = [], [], []
    all_field_scores = defaultdict(list)

    for pred_file in pred_files:
        pred_rules = _load_rules(str(pred_file), pred_file.name)
        doc_id = pred_file.stem.split("_")[0]
        # Filter GT to only rules for this document
        doc_gt_index = defaultdict(list, {
            k: v for k, v in gt_index.items()
            if v and v[0]["source"]["document_id"] == doc_id
        })
        n_doc_gt = sum(len(v) for v in doc_gt_index.values())
        matched, missing, extra = _match_rules(doc_gt_index, _index_by_clause(pred_rules))
        metrics = _compute_metrics(matched, n_doc_gt, len(pred_rules))
        field_scores = _compute_field_scores(matched)
        per_doc[pred_file.stem] = (metrics, field_scores)
        all_matched.extend(matched)
        all_missing.extend(missing)
        all_extra.extend(extra)
        for f, s in field_scores.items():
            all_field_scores[f].append(s)

    return per_doc, all_matched, all_missing, all_extra


# --- Detail report ---

def _write_detail_report(matched, missing, extra, out_path):
    lines = []
    lines.append("DETAILED EVALUATION REPORT")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("=" * 60)

    # Matched rules — show OK or WRONG with field diffs
    for gt, pred in matched:
        label = f"{gt['source']['section']}({gt['source']['clause']})"
        raw = gt["source"]["raw_text"]
        diffs = _diff_pair(gt, pred)
        if not diffs:
            lines.append(f"\n[OK]    {label}")
            lines.append(f'  "{raw}"')
        else:
            lines.append(f"\n[WRONG] {label}")
            lines.append(f'  "{raw}"')
            for field, gv, pv in diffs:
                lines.append(f"  {field:<26}  GT:   {gv}")
                lines.append(f"  {'':<26}  PRED: {pv}")

    # Missing rules — in GT but not extracted
    for r in missing:
        label = f"{r['source']['section']}({r['source']['clause']})"
        raw = r["source"]["raw_text"]
        lines.append(f"\n[MISSING] {label}")
        lines.append(f'  "{raw}"')
        lines.append(f"  GT applies_to:   {r.get('applies_to')}")

    # Extra rules — extracted but not in GT
    for r in extra:
        label = f"{r['source']['section']}({r['source']['clause']})"
        raw = r["source"]["raw_text"]
        lines.append(f"\n[EXTRA]   {label}")
        lines.append(f'  "{raw}"')
        lines.append(f"  PRED applies_to:  {r.get('applies_to')}")

    lines.append("\n" + "=" * 60)
    out_path.write_text("\n".join(lines), encoding="utf-8")


# --- Report printing ---

def _pct(v):
    return f"{v * 100:.0f}%"


def _print_single_report(metrics, field_scores, missing, extra, n_gt, n_pred):
    sep = "=" * 53
    print(f"\n{sep}")
    print("              EVALUATION REPORT")
    print(sep)
    print(f"\nRules")
    print(f"  Ground truth:        {n_gt:>4}")
    print(f"  Extracted:           {n_pred:>4}")
    print(f"  Matched:             {metrics['n_matched']:>4}")
    print(f"  Missing:             {len(missing):>4}")
    print(f"  Extra:               {len(extra):>4}")
    print(f"\nMetrics")
    print(f"  Precision:           {_pct(metrics['precision']):>5}")
    print(f"  Recall:              {_pct(metrics['recall']):>5}")
    print(f"  F1:                  {_pct(metrics['f1']):>5}")
    print(f"  Field accuracy:      {_pct(_field_avg(field_scores)):>5}")
    print(f"\nField breakdown")
    for field, score in field_scores.items():
        print(f"  {field:<26} {_pct(score):>5}")
    if missing:
        print(f"\nMissing rules")
        for r in missing:
            print(f"  {r['source']['section']}({r['source']['clause']})")
    if extra:
        print(f"\nExtra rules")
        for r in extra:
            print(f"  {r['source']['section']}({r['source']['clause']})")
    print(f"\n{sep}\n")


def _print_aggregate_report(per_doc, agg_metrics, agg_fields, missing, extra):
    sep = "=" * 53
    print(f"\n{sep}")
    print("              EVALUATION REPORT")
    print(sep)
    print(f"\nPer document")
    for doc, (m, fs) in per_doc.items():
        print(f"  {doc:<18}  P: {_pct(m['precision']):<5} R: {_pct(m['recall']):<5} "
              f"F1: {_pct(m['f1']):<5} Fields: {_pct(_field_avg(fs))}")
    print(f"\nAggregate")
    print(f"  Precision:           {_pct(agg_metrics['precision']):>5}")
    print(f"  Recall:              {_pct(agg_metrics['recall']):>5}")
    print(f"  F1:                  {_pct(agg_metrics['f1']):>5}")
    print(f"  Field accuracy:      {_pct(_field_avg(agg_fields)):>5}")
    print(f"\nField breakdown (across all documents)")
    for field, score in agg_fields.items():
        print(f"  {field:<26} {_pct(score):>5}")
    print(f"\n  Missing rules:       {len(missing):>4}")
    print(f"  Extra rules:         {len(extra):>4}")
    print(f"\n{sep}\n")


def _parse_args():
    parser = argparse.ArgumentParser(description="Evaluate pipeline output against ground truth.")
    parser.add_argument("--predicted", required=True, help="Path to a pipeline output JSON file, or a directory (auto-aggregates)")
    parser.add_argument("--mode", choices=["clause", "section"], default="clause", help="clause or section mode — used when --predicted is a directory (default: clause)")
    parser.add_argument("--detail", action="store_true", help="Write rule-by-rule breakdown to a file")
    parser.add_argument("--output", default="output/eval_detail.txt", help="Output path for --detail report (default: output/eval_detail.txt)")
    return parser.parse_args()


if __name__ == "__main__":
    main()

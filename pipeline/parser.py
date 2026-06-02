import re
import sys

# Matches: SECTION 5.1 ELIGIBILITY CRITERIA:
SECTION_RE = re.compile(
    r"SECTION\s+(\d+\.\d+)\s+([A-Z][A-Z\s\-&]+?):\s*\n",
    re.MULTILINE,
)

# Matches clause markers like (a), (b), (i), (ii), (iii)
CLAUSE_RE = re.compile(r"^\s*\(([a-z]+)\)\s+", re.MULTILINE)


def parse_document(text: str) -> list[dict]:
    """Parse a policy document into a flat list of clause dicts."""
    print(f"[parser]  Parsing document ({len(text)} chars)")

    section_match = SECTION_RE.search(text)
    if not section_match:
        print("[parser]  ERROR: No section header found — expected 'SECTION X.Y TITLE:'")
        sys.exit(1)

    section = section_match.group(1)
    section_title = section_match.group(2).strip().title()
    body = text[section_match.end():]

    print(f"[parser]  Found section {section} — {section_title}")

    clauses = _split_clauses(body, section, section_title)
    print(f"[parser]  Extracted {len(clauses)} clauses")
    return clauses


def _split_clauses(body: str, section: str, section_title: str) -> list[dict]:
    """Split body text into individual clause dicts."""
    matches = list(CLAUSE_RE.finditer(body))

    if not matches:
        print(f"[parser]  ERROR: No clauses found in section {section}")
        sys.exit(1)

    preamble = body[: matches[0].start()].strip()
    section_context = preamble if preamble else None

    clauses = []
    for i, match in enumerate(matches):
        clause_id = match.group(1)
        text_start = match.end()
        # Last clause runs to end of body; all others end where the next clause starts
        text_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        raw_text = body[text_start:text_end].strip()

        clauses.append(
            {
                "section": section,
                "section_title": section_title,
                "section_context": section_context,
                "clause": clause_id,
                "raw_text": raw_text,
            }
        )

    return clauses

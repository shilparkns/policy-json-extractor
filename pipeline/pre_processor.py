import re

CLAUSE_START_RE = re.compile(r"^\s*\([a-z]+\)\s+")
SECTION_RE = re.compile(r"^SECTION\s+\d+\.\d+")


def clean(text: str) -> str:
    """Normalize raw document text before parsing."""
    text = text.replace("\xa0", " ")                          # non-breaking spaces
    text = text.replace("‘", "'").replace("’", "'") # smart single quotes
    text = text.replace("“", '"').replace("”", '"') # smart double quotes
    text = text.replace("—", "-").replace("–", "-") # em/en dashes
    lines = text.splitlines()
    lines = _strip_lines(lines)
    lines = _collapse_blank_lines(lines)
    lines = _rejoin_split_clauses(lines)
    result = "\n".join(lines)
    print(f"[pre_processor] Cleaned document ({len(text)} → {len(result)} chars)")
    return result


def _strip_lines(lines: list[str]) -> list[str]:
    return [line.strip() for line in lines]


def _collapse_blank_lines(lines: list[str]) -> list[str]:
    # Keep at most one blank line between paragraphs
    result = []
    prev_blank = False
    for line in lines:
        if line == "":
            if not prev_blank:
                result.append(line)
            prev_blank = True
        else:
            result.append(line)
            prev_blank = False
    return result


def _rejoin_split_clauses(lines: list[str]) -> list[str]:
    """Join continuation lines onto the clause line above them."""
    result = []
    for line in lines:
        # A continuation line is non-empty, doesn't start a new clause or section,
        # and follows a non-blank line
        is_continuation = (
            result
            and line
            and result[-1] != ""
            and not CLAUSE_START_RE.match(line)
            and not SECTION_RE.match(line)
        )
        if is_continuation:
            result[-1] = result[-1] + " " + line
        else:
            result.append(line)
    return result

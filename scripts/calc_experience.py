#!/usr/bin/env python3
import re
from datetime import date
from pathlib import Path

README = Path("README.md")

# Matches: "YY.MM ~ YY.MM" or "YY.MM ~"
PERIOD_RE = re.compile(r"(\d{2})\.(\d{2})\s*~\s*(\d{2}\.(\d{2}))?", re.IGNORECASE)

def yy_mm_to_ymd(yy: int, mm: int):
    # Assume recent years => 20YY
    yyyy = 2000 + yy
    return yyyy, mm, 1

def diff_months(sy, sm, ey, em, sd=1, ed=1):
    months = (ey - sy) * 12 + (em - sm)
    if ed < sd:
        months -= 1
    return max(months, 0)

def parse_period_cell(cell_text: str):
    m = PERIOD_RE.search(cell_text)
    if not m:
        return None
    sy, sm = int(m.group(1)), int(m.group(2))
    ey_em = m.group(3)
    if ey_em:
        ey, em = map(int, ey_em.split("."))
    else:
        today = date.today()
        ey, em = today.year % 100, today.month
    return (sy, sm, ey, em)

def sum_experience_months(markdown: str) -> int:
    total = 0
    for line in markdown.splitlines():
        if '|' not in line or line.strip().startswith('|---'):
            continue
        cells = [c.strip() for c in line.split('|')]
        if len(cells) < 4:
            continue
        period_cell = cells[3]  # |Company|Position|Period| => index 3
        parsed = parse_period_cell(period_cell)
        if not parsed:
            continue
        sy, sm, ey, em = parsed
        sy_full, sm_full, _ = yy_mm_to_ymd(sy, sm)
        ey_full = 2000 + ey
        months = diff_months(sy_full, sm_full, ey_full, em)
        total += months
    return total

def format_years_months_en(total_months: int) -> str:
    y, m = divmod(total_months, 12)
    parts = []
    if y:
        parts.append(f"{y} year" + ("s" if y != 1 else ""))
    if m or not parts:
        parts.append(f"{m} month" + ("s" if m != 1 else ""))
    return " ".join(parts)

def main():
    md = README.read_text(encoding="utf-8")
    total_months = sum_experience_months(md)
    pretty = format_years_months_en(total_months)

    # Block placeholder (optional section)
    new_block = f"<!--TOTAL_EXP_START-->\nTotal experience: {pretty}\n<!--TOTAL_EXP_END-->"
    md = re.sub(
        r"<!--TOTAL_EXP_START-->.*?<!--TOTAL_EXP_END-->",
        new_block,
        md,
        flags=re.DOTALL
    )

    # Inline placeholder (header line)
    new_inline = f"<!--TOTAL_EXP_INLINE_START-->(Total: {pretty})<!--TOTAL_EXP_INLINE_END-->"
    md = re.sub(
        r"<!--TOTAL_EXP_INLINE_START-->.*?<!--TOTAL_EXP_INLINE_END-->",
        new_inline,
        md,
        flags=re.DOTALL
    )

    README.write_text(md, encoding="utf-8")

if __name__ == "__main__":
    main()

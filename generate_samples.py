#!/usr/bin/env python3
"""Create a *pipe-delimited* (|) table:

file_name |
titles_Vx | articles_Vx | sections_Vx | clauses_Vx   …for every discovered Vx

• One row per agreement HTML in time_to_get_real/html_files
• Agreement text cells are JSON-safe (quoted) and truncated to 500 chars
• Uses ‘|’ delimiter so Excel won’t split on commas in the texts
"""

from __future__ import annotations

import csv
import importlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sec_parser.processing_engine.core import AbstractSemanticElementParser

HTML_DIR = Path("time_to_get_real/html_files")
HTML_FILES = sorted(HTML_DIR.glob("*.html"))
if not HTML_FILES:
    sys.exit(f"❌  No HTML files found in: {HTML_DIR.resolve()}")

PARSER_MODULES = [f"agreement_parser_v{v}" for v in range(5, 10)]
STRUCT_TYPES = ("AgreementTitleElement", "ArticleElement", "SectionElement", "ClauseElement")


def load_parsers() -> list[type[AbstractSemanticElementParser]]:
    parsers: list[type[AbstractSemanticElementParser]] = []
    for mod_name in PARSER_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError:
            continue
        parsers.extend(getattr(mod, attr) for attr in dir(mod) if attr.startswith("AgreementParser") and callable(getattr(mod, attr)))  # type: ignore[arg-type]
    if not parsers:
        sys.exit("❌  No AgreementParserVX classes discovered.")
    return parsers


PARSERS = load_parsers()


def truncate(txt: str, max_len: int = 500) -> str:
    return txt[: max_len - 1] + "…" if len(txt) > max_len else txt


def collect_texts(elements: Iterable[Any], wanted_type: str) -> str:
    parts = [truncate(getattr(el, "text", "").strip()) for el in elements if type(el).__name__ == wanted_type]
    # Represent as JSON array so Excel keeps it in one cell
    return json.dumps(parts, ensure_ascii=False)


# --------------------------------- header ---------------------------------- #

header: list[str] = ["file_name"]
for parser_cls in PARSERS:
    vx = parser_cls.__name__.removeprefix("AgreementParser")
    header += [f"titles_{vx}", f"articles_{vx}", f"sections_{vx}", f"clauses_{vx}"]

# ---------------------------------- rows ----------------------------------- #

rows: list[list[str]] = []

for html_path in HTML_FILES:
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    row: list[str] = [html_path.name]

    for parser_cls in PARSERS:
        try:
            elements = parser_cls().parse(html)
        except Exception:
            row += ["[]", "[]", "[]", "[]"]  # keep shape
            continue

        row += [
            collect_texts(elements, "AgreementTitleElement"),
            collect_texts(elements, "ArticleElement"),
            collect_texts(elements, "SectionElement"),
            collect_texts(elements, "ClauseElement"),
        ]

    rows.append(row)

# ---------------------------------- write ---------------------------------- #

OUT_CSV = Path("samples_structural_contents_by_parser.csv")
with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f, delimiter="|", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(header)
    writer.writerows(rows)

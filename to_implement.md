Arthur — thanks for the detailed dump. I read through the v3/v4 snippets and the failing trees. Short version: your failures cluster into a handful of deterministic issues in the heading/outline detection and hierarchy assembly, plus some EDGAR‑specific HTML oddities (Workiva/Wdesk, Broadridge PROfile, table‑driven layouts, “white 1px text” overlays, and image‑only pages).

Below is a focused plan to drive the fail rate down toward zero without LLMs, with very concrete code changes you can drop in. I’m opinionated here; if anything feels off, say so and I’ll adjust.

⸻

0) What’s actually going wrong (from your examples)
	1.	Headings recognized but orphaned
You promote lots of lines as “Heading (L1)” but never attach them (e.g., agreement_004, 009, 014). This is almost entirely a hierarchy builder problem: your level math and stack logic don’t line up with the numbering schemes you detect.
	2.	Too many false positives for headings
Lines like “commission (a ‘Government Agency’)…” show up as L1 headings (agreement_001), and some random mid‑paragraph sentences are promoted.
	3.	Insufficient coverage of numbering schemes
You handle Section 3.2, and 1. Title, and ARTICLE II, but miss or mis‑level:
	•	I. / II. / III. (roman top‑level without the word “ARTICLE”)
	•	A. / (a) / (i) / (1) chains (clause levels)
	•	Section 1.1(a)(i) style mixed chains
	•	Table of Contents rows (table cells; first cell is “1.2”, second is the title)
	4.	EDGAR vendor quirks
	•	Workiva/Wdesk (data-wdesk, BRPFPageBreakArea, lots of <font>, center bold underlined titles).
	•	Broadridge PROfile (page bars/HRs, table TOCs).
	•	Scanned or image‑based pages (agreement_034, 085, 089, 098), and “1px white text” overlays (agreement_002) where the real text is tucked in a single, tiny white paragraph.
	5.	Dedup logic discards legitimate repeats
seen_sections keyed by section number alone will wrongly suppress sections that share the same decimal (e.g., repeated “1.2” under different Articles, or in Exhibits).
	6.	Document title vs first numbered provision
You sometimes set the agreement “title” from the first numbered provision (e.g., agreement_004 shows “AGREEMENT: 4. …”). That’s a title detector bug: center/bold/underlined block at top should be the title, not the first numbered heading.

⸻

1) Deterministic fixes that move the needle

A) Fix the level math + robust numbering normalizer

Your V6 does:

level = len(section_num.split('.')) + 1  # <- this pushes everything one too deep

Change it to:

level = len(section_num.split('.'))
if level == 0:
    level = 1

…and (crucially) stop assuming every heading that matches a pattern is level≥2. Numeric 1 should be level 1; 1.2 → level 2; 1.2.3 → level 3.

Next, normalize enumerators into a uniform path so stack logic becomes trivial. Examples:
	•	ARTICLE II → II (mark article=True, level 1 unless a separate “Section” follows).
	•	Section 3.2(a)(i) → path 3.2.a.i → levels 1,2,3,4.
	•	A. → .A (attach under the last non‑clause heading).
	•	(a) / (i) / (1) → a / i / 1 with parentheses stripped; track type to avoid misnesting.

Design a small, fully deterministic enumerator tokenization:

ROMAN = r"(?:M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3}))"
ART = re.compile(r"^(?i)\s*ARTICLE\s+(?P<roman>"+ROMAN+"|\d+)\b(?:\s*[:.\-–—]\s*(?P<title>.+))?$")
SEC = re.compile(r"^(?i)\s*SECTION\s+(?P<num>\d+(?:\.\d+)*)(?:\s*[:.\-–—]\s*(?P<title>.+))?$")
NUM = re.compile(r"^(?P<num>\d+(?:\.\d+)*)(?:[.)])\s+(?P<title>.+)$")
ROM = re.compile(r"^(?P<roman>"+ROMAN+")[.)]\s+(?P<title>.+)$")
UPA = re.compile(r"^(?P<uc>[A-Z])[.)]\s+(?P<title>.+)$")
LOA = re.compile(r"^\((?P<lc>[a-z])\)\s+(?P<title>.+)$")
LOR = re.compile(r"^\((?P<ri>i{1,9}|iv|ix|vi{0,3})\)\s+(?P<title>.+)$")  # roman (i),(ii)...
LOD = re.compile(r"^\((?P<dn>\d+)\)\s+(?P<title>.+)$")

	•	For NUM we also want to split title at the first period/colon when present, to avoid swallowing the paragraph. E.g., "4. Without Cause. If Employee’s..." → title "Without Cause", and the rest becomes the first paragraph under 4. This one change will clean up a huge chunk of “Heading (L1)” orphans.

B) Make heading detection scored and conservative

Right now it’s “regex → heading”. Add a tiny, deterministic score and a threshold:
	•	+2 if matches ARTICLE / SECTION.
	•	+1 if line starts with enumerator pattern (NUM, ROM, UPA, LOA, LOR, LOD).
	•	+1 if bold or underline tag is present (or font-weight ≥ 600).
	•	+1 if centered (align=center or CSS text-align center).
	•	+1 if ALLCAPS ratio ≥ 0.7 for the title strip.
	•	−2 if line starts with boilerplate keywords (IN WITNESS WHEREOF, By:, /s/, Name:, Title:, Date:).
	•	−1 if line > 140 chars before we split off the trailing paragraph.
	•	−1 if ends with comma/semicolon (typical body lines).
	•	−1 if number of tokens in the title > 15.

Threshold: ≥2 to classify as heading. Everything else stays as body.

This is still fully deterministic but slashes false positives.

C) Split “inline heading + body” on the first terminator

Implement a safe splitter:

def split_inline_title(title_plus, terminators=(". ", ": ", " - ")):
    for t in terminators:
        i = title_plus.find(t)
        if i > 0 and i <= 80:      # heading title rarely > ~80 chars
            return title_plus[:i].strip(), title_plus[i+len(t):].strip()
    return title_plus.strip(), ""  # no split

Apply it to NUM, ROM, UPA, LOA, etc. If you extract non‑empty rest, insert it as the first paragraph under that heading (don’t drop it).

D) Build a real hierarchy stack (and fix orphaning)

Maintain a stack of tuples (level, node_ref, kind) where kind ∈ {"article","section","sub","clause"}. For each new heading:
	1.	Compute its numeric level from the enumerator path length (see A).
	2.	While stack and stack[-1].level >= level, pop.
	3.	Attach the new node under stack[-1] (or root if empty).
	4.	Push new (level,node,kind).

Special cases:
	•	If you just saw ARTICLE and the next heading is Section X.Y, that’s inside the article; let “Section” win the numeric level.
	•	If your next enumerator is a letter/roman/digit in parens and there is no numeric/Section above, treat it as clause under the last visible numeric level (fallback to level 2 under the last top‑level if necessary). This avoids promoting (a) to root.

E) Expand table handling deterministically

Your _process_table_element currently only checks ^\d+\.?$ in the first <td>. Extend:

cell = first_cell.strip()
title = second_cell.strip()

if re.match(r"^\d+(?:\.\d+)*\.?$", cell):          # 1 or 1.2 or 1.2.3
    num = cell.rstrip(".")
    return SectionElement(..., section_number=num, section_title=title, level=len(num.split(".")))

if re.match(r"^(?i)ARTICLE\s+(?:\d+|"+ROMAN+")$", cell):
    art = cell.split(None,1)[1]
    return ArticleElement(..., article_number=art, article_title=title)

if re.match(r"^"+ROMAN+r"[.)]$", cell):             # I. II. III.
    return ArticleElement(..., article_number=cell.rstrip(".)"), article_title=title)

if re.match(r"^[A-Z][.)]$", cell):                  # A.
    return ClauseElement(..., enumerator=cell[0], title=title, level=calc_clause_level(...))

if re.match(r"^\([a-z]\)$", cell) or re.match(r"^\([ivxlcdm]+\)$", cell) or re.match(r"^\(\d+\)$", cell):
    # clause rows like (a) ...; infer level from current stack

Also add a robust signature table filter (you started this: good) and a TOC table filter: if any row contains “TABLE OF CONTENTS” or a majority of rows have a rightmost numeric page column and left column looks like headings, mark as TOC and harvest rather than treat as content.

F) Don’t dedupe by number alone

Instead of:

key = f'section:{element.section_number}'

Use document offsets + title:

def _key(el):
    tag = el.html_tag.get_bs4() if hasattr(el.html_tag, 'get_bs4') else None
    start = getattr(tag, 'sourceline', None) or 0
    title = getattr(el, 'section_title', None) or getattr(el, 'article_title', '') or ''
    num = getattr(el, 'section_number', None) or getattr(el, 'article_number', '')
    return f"{el.__class__.__name__}:{num}:{title.strip()[:40]}:{start}"

This keeps distinct “4.2 Notices” in different attachments/exhibits.

G) Vendor & boilerplate handling (deterministic)
	•	Title detector (run once, before heading classification): pick the first block that is (centered or H1/H2 or bold+underline) and ≤ 120 chars, not starting with “Exhibit …” or “Execution Version”. Set that as the agreement title and do not treat it as a numbered heading. This alone fixes the AGREEMENT: 4. … mis‑title in agreement_004.
	•	Workiva/Wdesk: keep your is_wdesk_document and add a pass that drops elements with classes like BRPFPageBreak, BRPFPageBreakArea, and normalizes <font> wrappers into plain text. These introduce tons of spurious blocks.
	•	Broadridge PROfile: drop top and bottom HR bars and their containers; they create fake blank lines that break grouping.
	•	“Exhibit 10.x”, “Execution Version”, “THE SECURITIES …” at the very top: classify as boilerplate (IrrelevantElement) unless you’re parsing a Subscription Agreement cover sheet; then keep, but not as headings.
	•	1px white overlays (agreement_002): do not discard solely based on color:white; font-size:1px. If the document has ≥1 image and a single long white 1px block, treat that block as the text layer and run the normal pipeline on it (you can temporarily strip the style and split it into sentences first). This saves you from needing OCR while still recovering the text (many SEC “image pages” hide this layer).
	•	Image‑only docs (no hidden text and lots of <img>): deterministically mark as image_only=True. If OCR isn’t allowed in this project, fail with a typed reason rather than “0 elements”. (If you can pull in pytesseract, a simple OCR fallback with a layout grid improves coverage, but I’ll respect “no ML models” unless you want it.)

H) TOC harvesting before classification

You already note “Expanded TOC support”. Make it strict:
	•	Detect block "TABLE OF CONTENTS" (case‑insensitive) within the first ~15% of the file (by source line).
	•	For each following line/row until an empty line or another big header:
	•	Match items like ARTICLE I — [Title] … [page], Section 3.2  — [Title] … [page], 1.2  [Title].
	•	Normalize the enumerator (as in A) and record (path, title).
	•	Later, when classifying headings, boost score (+2) if the same (path, title_prefix) exists in TOC. This collapses false positives and improves numbering disambiguation (roman‑as‑Article vs roman‑as‑clause).

I) Clause extraction within sections (lettered/roman/digit chains)

Within each section’s body, scan line‑starts for enumerators (a), (i), (1), A. and promote them to clauses with increasing clause levels. Do not promote if the previous non‑blank body line doesn’t end with the typical “heading terminator” (period/colon/dash) — this is a simple guard to avoid mid‑sentence (a) false positives.

J) Guardrails against signature/acknowledgment noise

You already added SignatureMetadataRemover. Add a terminator step near the end: when you encounter a block whose normalized text starts with IN WITNESS WHEREOF or SIGNATURES or contains /s/, switch the parser into signature mode and demote every following block to Irrelevant (except “Exhibit” headings which you may still want to harvest).

⸻

2) Concrete drop‑in changes (tight code)

Here’s a minimal but high‑leverage rewrite of your classifier that:
	•	expands pattern coverage,
	•	adds scoring,
	•	splits inline heading+body,
	•	fixes level math,
	•	avoids naive dedup.

You can replace your SmartSectionClassifierV6 core bits with this. I’ll keep your class/interface intact and only change internals.

# --- inside starting_anew_parser_v4.py (and v3), replace the internals of SmartSectionClassifierV6 ---

ROMAN = r"(?:M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))"
ART_RE = re.compile(r"^(?i)\s*ARTICLE\s+(?P<id>"+ROMAN+r"|\d+)\b(?:\s*[:.\-–—]\s*(?P<title>.+))?$")
SEC_RE = re.compile(r"^(?i)\s*SECTION\s+(?P<num>\d+(?:\.\d+)*)(?:\s*[:.\-–—]\s*(?P<title>.+))?$")
NUM_RE = re.compile(r"^(?P<num>\d+(?:\.\d+)*)(?:[.)])\s+(?P<title>.+)$")
ROM_RE = re.compile(r"^(?P<roman>"+ROMAN+r")[.)]\s+(?P<title>.+)$", re.IGNORECASE)
UPA_RE = re.compile(r"^(?P<uc>[A-Z])[.)]\s+(?P<title>.+)$")
LOA_RE = re.compile(r"^\((?P<lc>[a-z])\)\s+(?P<title>.+)$")
LOR_RE = re.compile(r"^\((?P<ri>i{1,9}|iv|ix|vi{0,3})\)\s+(?P<title>.+)$", re.IGNORECASE)
LOD_RE = re.compile(r"^\((?P<dn>\d+)\)\s+(?P<title>.+)$")

BOILERPLATE_PREFIXES = tuple([
    "IN WITNESS WHEREOF", "IN WITNESS", "SIGNATURES", "ACKNOWLEDG", "ATTEST:",
    "By:", "Name:", "Title:", "Date:", "/s/"
])

def _is_boilerplate_start(s: str) -> bool:
    t = s.strip().upper()
    return any(t.startswith(p) for p in BOILERPLATE_PREFIXES)

def _allcaps_ratio(s: str) -> float:
    letters = [c for c in s if c.isalpha()]
    if not letters: return 0.0
    return sum(1 for c in letters if c.isupper())/len(letters)

def _split_inline_title(s: str) -> tuple[str, str]:
    # split heading line into (title, rest-of-paragraph)
    for t in (". ", ": ", " - "):
        i = s.find(t)
        if 0 < i <= 80:
            return s[:i].strip(), s[i+len(t):].strip()
    return s.strip(), ""

def _calc_level_from_path(path: str) -> int:
    # "1.2.a.i" -> 4 ; "I" -> 1
    parts = [p for p in re.split(r"[.]", path) if p]
    return max(1, len(parts))

def _make_key(el) -> str:
    try:
        bs = el.html_tag.get_bs4()
        start = getattr(bs, 'sourceline', None) or 0
    except Exception:
        start = 0
    title = getattr(el, 'section_title', None) or getattr(el, 'article_title', '') or ''
    num = getattr(el, 'section_number', None) or getattr(el, 'article_number', '')
    return f"{el.__class__.__name__}:{num}:{title.strip()[:40]}:{start}"

class SmartSectionClassifierV6(AbstractElementwiseProcessingStep):
    def __init__(self, types_to_process=None) -> None:
        super().__init__(types_to_process=types_to_process or {NotYetClassifiedElement, TextElement, TableElement})
        self.seen = set()

    def _process_element(self, element: AbstractSemanticElement, context=None) -> AbstractSemanticElement | None:
        if not element.html_tag:
            return element

        name = element.html_tag.name.lower()
        if name == 'table':
            res = self._process_table_element(element)
            if isinstance(res, (ArticleElement, SectionElement, ClauseElement)):
                k = _make_key(res)
                if k in self.seen:
                    return element
                self.seen.add(k)
                return res
            return res

        text = element.html_tag.text.strip()
        if not text:
            return element

        res = self._extract_structured_element(text, element.html_tag)
        if isinstance(res, (ArticleElement, SectionElement, ClauseElement)):
            k = _make_key(res)
            if k in self.seen:
                return element
            self.seen.add(k)
            return res

        return res or element

    def _score_heading(self, raw: str, html_tag: HtmlTag) -> tuple[int, dict]:
        s = raw.strip()
        meta = {"bold": False, "underline": False, "center": False}

        try:
            bs = html_tag.get_bs4()
            tagname = getattr(bs, 'name', '').lower()
            if tagname in ('h1','h2','h3','h4'):
                meta["bold"] = True
            # simple checks
            style = (bs.get('style','') or '').lower()
            if 'text-align:center' in style or getattr(bs, 'get', lambda *_: None)('align') == 'center':
                meta["center"] = True
            # bold/underline tags
            if bs.find(['b','strong']): meta["bold"] = True
            if bs.find(['u']): meta["underline"] = True
        except Exception:
            pass

        score = 0
        if ART_RE.match(s) or SEC_RE.match(s): score += 2
        if NUM_RE.match(s) or ROM_RE.match(s) or UPA_RE.match(s) or LOA_RE.match(s) or LOR_RE.match(s) or LOD_RE.match(s):
            score += 1
        if meta["bold"] or meta["underline"]: score += 1
        if meta["center"]: score += 1

        if _is_boilerplate_start(s): score -= 2
        if len(s) > 140: score -= 1
        # roughly penalize likely body lines
        if s.endswith((",", ";")): score -= 1

        # caps boost
        m = ART_RE.match(s) or SEC_RE.match(s) or NUM_RE.match(s) or ROM_RE.match(s)
        title_preview = (m.group('title') if m and 'title' in m.groupdict() and m.group('title') else s)
        if _allcaps_ratio(title_preview) >= 0.7:
            score += 1

        return score, meta

    def _extract_structured_element(self, text: str, html_tag: HtmlTag):
        s = text.strip()

        # Hard stop for signatures/boilerplate lines
        if _is_boilerplate_start(s):
            return IrrelevantElement.create_from_element(TextElement(html_tag), log_origin=self.__class__.__name__)

        # Articles
        m = ART_RE.match(s)
        if m:
            art = m.group('id')
            title = (m.group('title') or '').strip()
            return ArticleElement(html_tag, article_number=str(art), article_title=title or '')

        # Sections
        m = SEC_RE.match(s)
        if m:
            num = m.group('num')
            title_all = (m.group('title') or '').strip()
            title, rest = _split_inline_title(title_all) if title_all else (title_all, "")
            elem = SectionElement(html_tag, section_number=num, section_title=title, level=_calc_level_from_path(num))
            if rest:
                # attach rest as first paragraph via a clone
                pass  # leave to downstream merger step; we only classify here
            return elem

        # Numeric / roman / letter headings
        for kind, regex in (('number', NUM_RE), ('roman', ROM_RE), ('uc', UPA_RE), ('lc', LOA_RE), ('ri', LOR_RE), ('dn', LOD_RE)):
            m = regex.match(s)
            if m:
                title_all = m.group('title').strip()
                title, rest = _split_inline_title(title_all)
                if kind == 'number':
                    num = m.group('num')
                    level = _calc_level_from_path(num)
                    return SectionElement(html_tag, section_number=num, section_title=title, level=level)
                elif kind == 'roman':
                    r = m.group('roman')
                    return ArticleElement(html_tag, article_number=r.upper(), article_title=title)
                else:
                    # clause under current context; mark level unknown here (resolved in hierarchy builder)
                    enum = m.groupdict().get('uc') or m.groupdict().get('lc') or m.groupdict().get('ri') or m.groupdict().get('dn')
                    return ClauseElement(html_tag, enumerator=str(enum), clause_title=title)

        # Low score? keep as body
        score, _meta = self._score_heading(s, html_tag)
        if score >= 2:
            # treat as a generic L1 heading fallback if we can't parse a number (rare)
            return SectionElement(html_tag, section_number="", section_title=s, level=1)

        return None

    def _process_table_element(self, element: AbstractSemanticElement):
        try:
            if not hasattr(element.html_tag, '_bs4'):
                return element
            tds = element.html_tag._bs4.find_all('td')
            if len(tds) < 1: return element

            first_cell = tds[0].get_text(" ", strip=True)
            second_cell = tds[1].get_text(" ", strip=True) if len(tds) >= 2 else ""

            # TOC row short-circuit (do not classify as content headings)
            if "TABLE OF CONTENTS" in (first_cell.upper() + " " + second_cell.upper()):
                return IrrelevantElement.create_from_element(element, log_origin=self.__class__.__name__)

            row = (first_cell + " " + second_cell).strip()

            # article/section patterns in table rows
            for regex in (ART_RE, SEC_RE, NUM_RE, ROM_RE, UPA_RE, LOA_RE, LOR_RE, LOD_RE):
                m = regex.match(row)
                if m:
                    # reuse the text path
                    fake_tag = element.html_tag  # good enough, we only need classification
                    return self._extract_structured_element(row, fake_tag)

            # signature table?
            if self._is_signature_table(first_cell, second_cell):
                return SignatureBlockElement(element.html_tag)

        except Exception:
            pass

        return element

Notes
• I referenced ClauseElement and SignatureBlockElement since your tree prints show clauses and signatures; if you don’t have ClauseElement, you can map those to SectionElement with a clause=True flag and a higher level.
• The “rest of paragraph under heading” hookup is best done in your merger step, not here, to keep this classifier pure.

K) Title detector (tiny, effective)

Add a pre‑pass step (run once, very early):

class TitleDetector(AbstractElementwiseProcessingStep):
    def _process_element(self, element, context=None):
        # set context.title once and mark element as IrrelevantElement to avoid re-classifying as heading
        if getattr(context, "doc_title_set", False):
            return element
        if not element.html_tag:
            return element
        txt = element.html_tag.text.strip()
        if not txt or txt.lower().startswith(("exhibit", "execution version")):
            return element
        try:
            bs = element.html_tag.get_bs4()
            style = (bs.get('style','') or '').lower()
            centered = ('text-align:center' in style) or (bs.get('align') == 'center')
            bold = bool(bs.find(['b','strong']))
            under = bool(bs.find(['u']))
        except Exception:
            centered = bold = under = False

        if centered and (bold or under) and len(txt) <= 120:
            context.doc_title = txt
            context.doc_title_set = True
            return IrrelevantElement.create_from_element(element, log_origin=self.__class__.__name__)
        return element

Wire this near the top of your pipeline, before TOC and SmartSectionClassifierV6.

L) Hierarchy builder (stack, not de‑dupe)

If your pipeline currently relies on implicit nesting, add an explicit HierarchyAssembler step that consumes the stream of ArticleElement/SectionElement/ClauseElement/TextElement and attaches nodes according to the stack rules from D. That’s where you’ll assign clause levels for (a)/(i)/(1) based on the current stack head.

⸻

3) Why these changes fix the specific “D”s you showed
	•	agreement_001 (Employment Amendment): The bogus headings (“commission (a ‘Government Agency’)…”) lose their score and won’t be promoted; “IN WITNESS WHEREOF” and signatures are demoted. Orphan rate drops because numeric items like “Section 7(b)…“ will be parsed as sub‑sections under the right section, not as top‑level.
	•	agreement_002 (Single‑tenant lease – hidden white 1px text): The “white overlay” rule will treat the tiny white block as text and run the normal classifier over it; you’ll get a full outline instead of 0 elements.
	•	agreement_004 / 009 / 014 / 020: Inline headings like “4. Without Cause. If …” will split at the first period, giving clean titles and attaching the remainder as body. The corrected level math fixes the rampant orphaning. Roman numerals without the word “ARTICLE” become articles.
	•	agreement_017 / 036 / 039 / 066 / 069 / 091 (Workiva/Director Comp policies): Vendor boilerplate and page‑break wrappers are stripped; bold/underline/center scoring and TOC boosting find real headings. Policies with simple numbered and lettered lists map into sections + clauses deterministically.
	•	Pure image docs (034, 085, 089, 098): You’ll consistently mark them as image_only=True. If you enable OCR later, that path is contained and won’t affect determinism elsewhere.

⸻

4) Pipeline order that’s been robust in practice
	1.	HtmlCommentRemoverStep (you have it)
	2.	TitleDetector (new, above)
	3.	SignatureMetadataRemover (you have it)
	4.	VendorCleaner (new: drop Workiva/Broadridge pagebreak wrappers; normalize font/align)
	5.	TableClassifier (existing)
	6.	TableOfContentsClassifier (harvest + store in context; don’t inject nodes yet)
	7.	TextElementMerger (merge broken lines; apply _split_inline_title if already marked as heading)
	8.	SmartSectionClassifierV6 (rewritten as above)
	9.	HierarchyAssembler (explicit stack)
	10.	EmptyElementClassifier (trim leftovers)

Keep your step tracer on; add counters for “score<2 demotions”, “inline‑split applied”, “signature mode entered”.

⸻

5) A few pragmatic thresholds (deterministic knobs)
	•	Max heading length before split: 120 chars (you set 120 — keep it).
	•	First inline split terminator position ≤ 80 chars.
	•	Clause chains: promote only if line starts with enumerator and the prior block was a heading or an empty line.
	•	TOC harvesting window: stop if you advance > 600 lines or hit another all‑caps centered block.

⸻

6) Quick tests to lock this down

Create fixtures that mirror the broken bits you pasted and assert the tree:
	•	Inline heading split:
Input: "4. Without Cause. If Employee’s …" → Section 4 with title Without Cause, body starts with If Employee’s ….
	•	Roman top‑level: "II. TERMINATION" without “ARTICLE” → Article II.
	•	Parens clauses under a section:
"(a) Confidentiality.", "(i) Definition." attach as levels 3 and 4 respectively.
	•	Table row section: first td "1.2.", second td "Notices" → Section 1.2: Notices.
	•	Boilerplate demotion: "IN WITNESS WHEREOF …" becomes Irrelevant and flips the parser into signature mode.
	•	White 1px overlay: from agreement_002, assert you see > 10 headings after treating the overlay as text.

⸻

7) Caveats (staying honest)
	•	Deterministic patterning will never be perfect for every bespoke layout. The TOC boost and inline‑split heuristics buy you most of the last mile; the rest is long‑tail.
	•	Image‑only exhibits genuinely need OCR (Tesseract is fine, deterministic). If OCR is off the table, you should intentionally classify them as “image_only” and exclude them from your failure metric (or report separately) — otherwise you’ll asymptotically sit a few points above 0% fail forever.
	•	If you rely on computed CSS to gauge bold/center, ensure cssutils is truly wired (you set _USE_CSSUTILS=True but I don’t see calls). The simple tag checks I used (<b>/<u>/align=center) work on most EDGAR conversions even without CSS inference.

⸻

If you want, I can turn the HierarchyAssembler into a concrete step for your sec_parser pipeline and sketch a tiny test harness that computes your “orphan rate” before and after these patches. Happy to iterate on the code in your actual repo layout.

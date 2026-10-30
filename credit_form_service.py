#!/usr/bin/env python3
"""Credit application form filling — analysis, AI field mapping, rendering.

Backs the /credit-form page in bpv-frontend. Where the per-supplier scripts
(fill_ub_credit_form.py, fill_mincom_credit_form.py) hard-code one fill() call
per field, this module finds the blanks in *any* blank form PDF, proposes what
goes in each, and renders the result — so a new supplier is a drag-and-drop
instead of a new script.

The three detectors in analyze() exist because no single one covers both known
form styles: the UB Emirates form is drawn rectangles, the Mincom form has zero
drawn rects and is entirely table cells and inline underscore blanks.

Coordinates are PDF points throughout, matching the fill() contract inherited
from fill_ub_credit_form.py.
"""

import os
import re
import io
import json
import time
import base64
from datetime import datetime

import fitz
from dotenv import load_dotenv

load_dotenv()

BLUE = (0, 0, 0.55)

# Preview raster scale. The frontend divides rendered pixel coords by this to
# get PDF points, so it must match what the browser receives.
PREVIEW_ZOOM = 2

# ============================================================================
# COMPANY PROFILE — single source of truth
# ============================================================================
# Mirrors the Standard Data table in directives/fill_credit_application.md.
# Change a value HERE (not in the individual fill_*.py scripts) — the directive
# table is a human-readable mirror and points back at this list.

def _profile_defaults():
    """Ordered field catalogue. Values marked dynamic are filled per request."""
    now = datetime.now()
    return [
        # -- Company ---------------------------------------------------------
        ("company_name",        "Company Name",         os.getenv('COMPANY_NAME', 'Newell Electromechanical Works'), "Company"),
        ("vat_trn",             "VAT Reg. No. (TRN)",   os.getenv('COMPANY_TRN', '100280024900003'), "Company"),
        ("company_type",        "Company Type",         "LLC", "Company"),
        ("address",             "Address",              os.getenv('COMPANY_ADDRESS', 'Jesco Building, Al Baraha, Deira, Dubai'), "Company"),
        ("address_short",       "Address (short)",      "Jesco Bldg, Al Baraha, Deira, Dubai", "Company"),
        ("po_box",              "P.O. Box",             "88593", "Company"),
        ("city",                "City",                 "Dubai", "Company"),
        ("building",            "Building",             "Jesco, 1st Floor", "Company"),
        ("tel",                 "Tel",                  "04-8843367", "Company"),
        ("email",               "Email",                "info@newellmepco.com", "Company"),
        ("website",             "Website",              "newellmepco.com", "Company"),
        ("year_established",    "Year Established",     "2013", "Company"),
        ("trade_license_no",    "Trade License No.",    "686985", "Company"),
        ("tl_expiry",           "T.L Expiry Date",      "14/04/2027", "Company"),
        ("nature_of_business",  "Nature of Business",   "Electromechanical / MEP Contracting", "Company"),

        # -- Director --------------------------------------------------------
        ("director_name",        "Director Name",       "Hesham Youssef",    "Director"),
        ("director_nationality", "Nationality",         "Egyptian",          "Director"),
        ("director_position",    "Position",            "Managing Partner",  "Director"),
        ("director_mobile",      "Contact No.",         "0506516343",        "Director"),
        ("passport_no",          "Passport No.",        "A39023285",         "Director"),

        # -- Contacts --------------------------------------------------------
        ("accounts_contact_name", "Accounts Contact",     "Seif",       "Contacts"),
        ("accounts_contact_tel",  "Accounts Tel",         "0565657381", "Contacts"),
        ("purchase_contact_name", "Purchase Contact",     "Mahmoud",    "Contacts"),
        ("purchase_contact_tel",  "Purchase Tel",         "0508852289", "Contacts"),

        # -- Bank ------------------------------------------------------------
        ("bank_name",       "Bank",        "Mashreq Bank",  "Bank"),
        ("bank_branch",     "Branch",      "Dubai",         "Bank"),
        ("bank_account_no", "Account No.", "019102185508",  "Bank"),
        # No IBAN on file for the Mashreq account either; per user instruction
        # (2026-07-07) the account number goes into any "IBAN No." field rather
        # than being left blank.
        ("bank_iban",       "IBAN No.",    "019102185508",  "Bank"),

        # -- Trade / credit references ---------------------------------------
        # Raneen Co is the only reference we give out (user instruction,
        # 2026-09-05). A form asking for two or three references gets this one
        # and the rest left blank, rather than names we no longer stand behind.
        ("ref1_company", "Ref A — Company", "Raneen Co",  "References"),
        ("ref1_contact", "Ref A — Contact", "Mohamed",    "References"),
        ("ref1_tel",     "Ref A — Tel",     "0526334341", "References"),
        ("ref1_limit",   "Ref A — Limit",   "300,000",    "References"),
        ("ref1_term",    "Ref A — Term",    "120 days",   "References"),

        # -- Credit request --------------------------------------------------
        ("credit_amount",    "Credit Amount Requested", "300,000", "Credit"),
        ("credit_term_days", "Credit Term",             "90 days", "Credit"),
        ("payment_terms",    "Payment Terms",           "PDC",     "Credit"),

        # -- Signatories -----------------------------------------------------
        ("signatory1_name",     "Signatory Name",     "Hesham Youssef",   "Signatories"),
        ("signatory1_position", "Signatory Position", "Managing Partner", "Signatories"),

        # -- Date (defaults to today, not a stale hardcoded date) -------------
        ("date_day",   "Date — Day",   now.strftime('%d'), "Date"),
        ("date_month", "Date — Month", now.strftime('%m'), "Date"),
        ("date_year",  "Date — Year",  now.strftime('%Y'), "Date"),
        ("date_full",  "Date (full)",  now.strftime('%d/%m/%Y'), "Date"),
    ]


def company_profile():
    """Profile as a list of {key, label, value, group} dicts."""
    return [{"key": k, "label": lbl, "value": v, "group": g}
            for k, lbl, v, g in _profile_defaults()]


def profile_values():
    """Flat {key: value} map."""
    return {k: v for k, _, v, _ in _profile_defaults()}


# ============================================================================
# RENDERING PRIMITIVES
# ============================================================================

def fill(page, x0, x1, y0, y1, text, size=10, color=BLUE):
    """Insert text centered horizontally and vertically in region (x0,y0)-(x1,y1).

    Carried over unchanged from fill_ub_credit_form.py so output from the UI
    matches what the per-supplier scripts already produce.
    """
    tw = fitz.get_text_length(text, fontname="helv", fontsize=size)
    # Horizontal: centered, but never starts before the left edge
    cx = max(x0 + 2, x0 + (x1 - x0 - tw) / 2)
    # Vertical: baseline = midpoint + 0.3*size  (ascender ~0.8s, descender ~0.2s)
    cy = (y0 + y1) / 2 + 0.3 * size
    page.insert_text(fitz.Point(cx, cy), text, fontsize=size, color=color)


def autosize(text, x0, x1, max_size=10.0, min_size=6.0):
    """Largest helv size <= max_size whose text fits the box width.

    Replaces the manual 10/9/8/7pt ladder from the directive as the default;
    an explicit per-placement size still wins.
    """
    if not text:
        return max_size
    avail = max(x1 - x0 - 4, 1)
    size = max_size
    while size > min_size:
        if fitz.get_text_length(text, fontname="helv", fontsize=size) <= avail:
            return round(size, 1)
        size -= 0.5
    return min_size


def _wrap(text, width, size):
    """Greedy word wrap into lines that fit `width` at `size`."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if fitz.get_text_length(trial, fontname="helv", fontsize=size) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _fill_multiline(page, x0, x1, y0, y1, text, size, color=BLUE, align='center'):
    """Left-aligned wrapped text inside a region.

    Used for free-text blocks the user adds (terms & conditions, notes) — the
    single-line fill() centers, which reads wrong for a paragraph. `align`
    controls the block vertically: 'center' for a box being filled in, 'top'
    when replacing a printed clause so the new wording starts where the old
    one did.
    """
    width = max(x1 - x0 - 4, 1)
    lines = _wrap(text, width, size)
    lh = size * 1.25
    if align == 'top':
        start = y0 + size * 0.95
    else:
        start = (y0 + y1) / 2 - (lh * len(lines)) / 2 + size * 0.85
    for i, line in enumerate(lines):
        page.insert_text(fitz.Point(x0 + 2, start + i * lh), line,
                         fontsize=size, color=color)


STRIKE = (0.75, 0, 0)


def _line_rects(page, rect):
    """Bounding box of each printed text line inside a region."""
    clip = fitz.Rect(rect)
    out = []
    try:
        d = page.get_text("dict", clip=clip)
    except Exception:
        return out
    for block in d.get('blocks', []):
        for line in block.get('lines', []):
            bb = line.get('bbox')
            if bb and bb[2] - bb[0] > 4:
                out.append(fitz.Rect(bb))
    return out


def _apply_edits(doc, edits):
    """Strike out, delete, or reword the supplier's own pre-printed clauses.

    Deletions and rewordings go through redaction, which genuinely removes the
    underlying text rather than covering it. All redactions on a page are
    applied in one pass BEFORE anything new is drawn, because applying them
    afterwards would erase what we just wrote.
    """
    by_page = {}
    for e in edits:
        idx = int(e.get('page', 0))
        if 0 <= idx < doc.page_count and e.get('mode') in ('strike', 'remove', 'replace'):
            by_page.setdefault(idx, []).append(e)

    for idx, page_edits in by_page.items():
        page = doc[idx]

        # Pass 1 — redact everything being removed or reworded.
        redacted = False
        for e in page_edits:
            if e['mode'] in ('remove', 'replace'):
                page.add_redact_annot(fitz.Rect(e['x0'], e['y0'], e['x1'], e['y1']),
                                      fill=(1, 1, 1))
                redacted = True
        if redacted:
            page.apply_redactions()

        # Pass 2 — draw strikes and replacement wording on the cleared page.
        for e in page_edits:
            rect = (float(e['x0']), float(e['y0']), float(e['x1']), float(e['y1']))
            if e['mode'] == 'strike':
                for lr in _line_rects(page, rect):
                    y = (lr.y0 + lr.y1) / 2
                    page.draw_line(fitz.Point(lr.x0, y), fitz.Point(lr.x1, y),
                                   color=STRIKE, width=1.1)
            elif e['mode'] == 'replace':
                text = (e.get('text') or '').strip()
                if text:
                    # Match the clause it replaces: same left margin, same size,
                    # starting on the same line — so the page still reads as one
                    # document rather than a patch.
                    left = float(e.get('text_x0') or rect[0])
                    _fill_multiline(page, left, rect[2], rect[1], rect[3], text,
                                    size=float(e.get('size') or 9), color=(0, 0, 0),
                                    align='top')


def render(pdf_bytes, placements, edits=None):
    """Draw `placements` onto the blank form and return the filled PDF bytes.

    Each placement: {page, x0, y0, x1, y1, text, kind, size?}. The frontend
    sends fully resolved text, so what the preview shows is what gets drawn.

    `edits` amend the supplier's own printed clauses — see _apply_edits.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if edits:
            _apply_edits(doc, edits)

        for p in placements:
            text = (p.get('text') or '').strip()
            kind = p.get('kind') or 'text'
            if not text and kind != 'checkbox':
                continue

            idx = int(p.get('page', 0))
            if idx < 0 or idx >= doc.page_count:
                continue
            page = doc[idx]

            x0, y0 = float(p['x0']), float(p['y0'])
            x1, y1 = float(p['x1']), float(p['y1'])

            if kind == 'checkbox':
                fill(page, x0, x1, y0, y1, "X", size=float(p.get('size') or 9))
            elif kind == 'multiline':
                _fill_multiline(page, x0, x1, y0, y1, text,
                                size=float(p.get('size') or 8))
            else:
                size = p.get('size') or autosize(text, x0, x1)
                fill(page, x0, x1, y0, y1, text, size=float(size))

        out = io.BytesIO()
        doc.save(out)
        return out.getvalue()
    finally:
        doc.close()


# ============================================================================
# ANALYSIS — find the blanks
# ============================================================================

_MAX_CANDIDATES_PER_PAGE = 140


_MIN_PARAGRAPH_WORDS = 12
_MIN_LOWERCASE_RATIO = 0.4


def _is_prose(text):
    """Running sentences, as opposed to a row of form labels.

    PyMuPDF returns a whole label row as one block ("Bank Name Branch Account
    No. Facilities Available"), which is long but is not a clause. Prose is
    distinguished by its function words: real sentences are mostly lowercase,
    label rows are mostly Capitalised.
    """
    words = text.split()
    if len(words) < _MIN_PARAGRAPH_WORDS:
        return False
    lower = sum(1 for w in words if w[:1].islower())
    return lower / len(words) >= _MIN_LOWERCASE_RATIO


def _first_glyph_x(lines):
    """Left edge of the first non-space character across a block's lines.

    Lines here often begin with space characters, and their bbox includes that
    whitespace — reading the bbox instead puts replacement wording 20pt left of
    the clause it replaces.
    """
    xs = []
    for ln in lines:
        for span in ln.get('spans') or []:
            for ch in span.get('chars') or []:
                if ch.get('c', ' ').strip():
                    xs.append(ch['bbox'][0])
                    break
            else:
                continue
            break
    return min(xs) if xs else None


def _detect_paragraphs(page, raw=None):
    """The supplier's own printed prose — the clauses we may need to amend.

    Only running text qualifies; form labels and table cells are short and are
    already handled as blanks.
    """
    out = []
    try:
        d = raw if raw is not None else page.get_text("rawdict")
    except Exception:
        return out

    for b in d.get('blocks', []):
        if b.get('type') != 0:           # 0 == text
            continue
        lines = b.get('lines') or []
        spans = [s for ln in lines for s in (ln.get('spans') or [])]
        # rawdict spans carry chars, dict spans carry text — accept either.
        span_text = [s['text'] if 'text' in s
                     else ''.join(c.get('c', '') for c in (s.get('chars') or []))
                     for s in spans]
        text = ' '.join(''.join(span_text).split())
        if not _is_prose(text):
            continue

        sizes = [round(s.get('size', 0), 1) for s, t in zip(spans, span_text) if t.strip()]
        bb = b['bbox']
        out.append({
            "x0": round(bb[0], 1), "y0": round(bb[1], 1),
            "x1": round(bb[2], 1), "y1": round(bb[3], 1),
            # Where the glyphs actually start and how big they are, so
            # replacement wording lines up with the clauses around it.
            "text_x0": round(_first_glyph_x(lines) or bb[0], 1),
            "size": max(set(sizes), key=sizes.count) if sizes else 9.0,
            "text": text[:600],
        })
    out.sort(key=lambda p: (p['y0'], p['x0']))
    return out


def _rects_overlap(a, b, tol=0.6):
    """True when two candidate rects cover substantially the same area."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return False
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / min(area_a, area_b) >= tol


def _detect_drawn_boxes(page):
    """Detector 1 — drawn input rectangles (the UB Emirates form style).

    Same rule as probe_credit_form.py: wide and shallow.
    """
    out = []
    for d in page.get_drawings():
        r = d.get('rect')
        if r is None:
            continue
        w, h = r.width, r.height
        if w > 20 and 4 < h < 40:
            out.append(([round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)], 'box'))
    return out


def _detect_underscore_runs(page, raw=None):
    """Detector 2 — inline `Name: ______` blanks (the Mincom form style).

    Uses rawdict so each underscore's own bbox is known; estimating the run
    from a whole word block is what the directive warns against, because the
    next label gets glued onto the end of the same block.
    """
    out = []
    try:
        raw = raw if raw is not None else page.get_text("rawdict")
    except Exception:
        return out

    for block in raw.get('blocks', []):
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                run = None
                for ch in span.get('chars', []):
                    if ch.get('c') == '_':
                        bb = ch['bbox']
                        if run is None:
                            run = [bb[0], bb[1], bb[2], bb[3], 1]
                        else:
                            run[0] = min(run[0], bb[0])
                            run[1] = min(run[1], bb[1])
                            run[2] = max(run[2], bb[2])
                            run[3] = max(run[3], bb[3])
                            run[4] += 1
                    else:
                        if run and run[4] >= 3:
                            out.append(run)
                        run = None
                if run and run[4] >= 3:
                    out.append(run)

    rects = []
    for x0, y0, x1, y1, _n in out:
        # Underscores sit on the baseline; lift the box so centered text lands
        # on the line rather than under it.
        h = max(y1 - y0, 1)
        rects.append(([round(x0, 1), round(y0 - 11 + h, 1), round(x1, 1), round(y1, 1)], 'blank'))
    return rects


def _detect_grid_cells(page):
    """Detector 3 — table cells from ruled lines (probe2's vline logic).

    Rows come from horizontal rules, columns from vertical ones; a cell is the
    intersection. Covers ruled tables that have no per-cell rectangle.
    """
    hlines, vlines = [], []
    for d in page.get_drawings():
        r = d.get('rect')
        if r is None:
            continue
        w, h = r.width, r.height
        if w > 40 and h <= 4:
            hlines.append((round((r.y0 + r.y1) / 2, 1), r.x0, r.x1))
        elif h > 10 and w <= 3:
            vlines.append((round((r.x0 + r.x1) / 2, 1), r.y0, r.y1))

    hlines.sort()
    ys = []
    for y, _a, _b in hlines:
        if not ys or abs(y - ys[-1]) > 2:
            ys.append(y)

    out = []
    for top, bottom in zip(ys, ys[1:]):
        height = bottom - top
        if not (10 <= height <= 45):
            continue
        # Column dividers that actually span this row band
        xs = sorted({x for x, vy0, vy1 in vlines
                     if vy0 <= top + 2 and vy1 >= bottom - 2})
        if len(xs) < 2:
            continue
        for left, right in zip(xs, xs[1:]):
            if right - left < 25:
                continue
            out.append(([round(left, 1), round(top, 1), round(right, 1), round(bottom, 1)], 'cell'))
    return out


_FILLER = re.compile(r'[_ ​]+')


def _is_noise(word):
    return set(word) <= {'_', '.', ':', '-', '/', '|'}


def _clean_label(text):
    """Drop underscore filler and stray whitespace from harvested label text."""
    return _FILLER.sub(' ', text or '').strip(' :.-|')


def _label_left(words, rect, with_gap=False):
    x0, y0, y1 = rect[0], rect[1], rect[3]
    cy = (y0 + y1) / 2
    cand = [w for w in words
            if w[2] <= x0 + 2 and w[1] < cy < w[3] and x0 - w[2] < 160
            and not _is_noise(w[4])]
    if not cand:
        return ('', 1e9) if with_gap else ''
    cand.sort(key=lambda w: -w[2])
    gap = x0 - cand[0][2]
    band = cand[0][3]
    picked = sorted([w for w in cand if abs(w[3] - band) < 4][:6], key=lambda w: w[0])
    text = ' '.join(w[4] for w in picked)[:70]
    return (text, gap) if with_gap else text


def _label_right(words, rect, with_gap=False):
    x1, y0, y1 = rect[2], rect[1], rect[3]
    cy = (y0 + y1) / 2
    cand = [w for w in words
            if w[0] >= x1 - 2 and w[1] < cy < w[3] and w[0] - x1 < 90
            and not _is_noise(w[4])]
    if not cand:
        return ('', 1e9) if with_gap else ''
    cand.sort(key=lambda w: w[0])
    gap = cand[0][0] - x1
    band = cand[0][3]
    picked = [w for w in cand if abs(w[3] - band) < 4][:4]
    text = ' '.join(w[4] for w in picked)[:70]
    return (text, gap) if with_gap else text


def _checkbox_label(words, rect):
    """The option a tick box stands for: the nearest word, on whichever side.

    Two things make this fiddly. Forms are inconsistent about the side even
    within one page — UB writes "[ ] LLC" but later "60Days [ ] 90days [ ]" —
    and a run of options sits on one baseline, so sweeping up every word to the
    left of the second box yields "60Days 90days" and matches the wrong option.
    Only the immediately adjacent word (plus a touching neighbour, for "60
    Days") identifies the box.
    """
    x0, x1, cy = rect[0], rect[2], (rect[1] + rect[3]) / 2
    row = [w for w in words if w[1] < cy < w[3] and not _is_noise(w[4])]

    left = sorted([w for w in row if w[2] <= x0 + 2], key=lambda w: -w[2])
    right = sorted([w for w in row if w[0] >= x1 - 2], key=lambda w: w[0])
    left_gap = x0 - left[0][2] if left else 1e9
    right_gap = right[0][0] - x1 if right else 1e9
    if left_gap > 90 and right_gap > 90:
        return ''

    if left_gap <= right_gap:
        picked = [left[0]]
        if len(left) > 1 and left[0][0] - left[1][2] < 4:
            picked.insert(0, left[1])
    else:
        picked = [right[0]]
        if len(right) > 1 and right[1][0] - right[0][2] < 4:
            picked.append(right[1])
    return _clean_label(' '.join(w[4] for w in picked))


def _label_above(words, rect):
    x0, y0, x1 = rect[0], rect[1], rect[2]
    cand = [w for w in words
            if w[3] <= y0 + 2 and y0 - w[3] < 24 and w[2] > x0 - 5 and w[0] < x1 + 5
            and not _is_noise(w[4])]
    if not cand:
        return ''
    cand.sort(key=lambda w: (-w[3], w[0]))
    band = cand[0][3]
    picked = sorted([w for w in cand if abs(w[3] - band) < 4][:6], key=lambda w: w[0])
    return ' '.join(w[4] for w in picked)[:70]


_SECTION_HEAD = re.compile(r'^\s*\d+\.?\s*[A-Za-z]')


def _section_headings(words):
    """Numbered section headings on a page, as (y_bottom, text) sorted by y.

    A form's sections say who a block of blanks is about — section 4 of the UB
    form is the *sponsor's* address, not ours — so the heading has to travel
    with each candidate.
    """
    lines = {}
    for w in words:
        lines.setdefault(round(w[3], 0), []).append(w)
    out = []
    for y, ws in lines.items():
        ws.sort(key=lambda w: w[0])
        text = ' '.join(w[4] for w in ws)
        if _SECTION_HEAD.match(text) and len(text) > 6:
            out.append((y, text[:90]))
    out.sort()
    return out


def _section_for(headings, rect):
    best = ''
    for y, text in headings:
        if y <= rect[1] + 2:
            best = text
        else:
            break
    return best


def _inner_words(words, rect):
    """Printed text fully inside a rect -> (text, right_edge), or None."""
    x0, y0, x1, y1 = rect
    inside = [w for w in words
              if w[0] >= x0 - 1 and w[2] <= x1 + 1 and w[1] >= y0 - 1 and w[3] <= y1 + 1
              and not _is_noise(w[4])]
    if not inside:
        return None
    inside.sort(key=lambda w: (w[1], w[0]))
    return (' '.join(w[4] for w in inside)[:70], max(w[2] for w in inside))


def _row_label(annotated, rect):
    """Text of the nearest filled cell on the same row, to the left.

    In a ruled table that is exactly the field name, which beats inferring one
    from loose nearby words.
    """
    x0, y0, y1 = rect[0], rect[1], rect[3]
    cy = (y0 + y1) / 2
    best, best_gap = '', 1e9
    for r, _kind, inner in annotated:
        if not inner or r[2] > x0 + 2 or not (r[1] < cy < r[3]):
            continue
        gap = x0 - r[2]
        if 0 <= gap < best_gap:
            best, best_gap = inner[0], gap
    return best if best_gap < 40 else ''


def _nearest_label(words, rect):
    """Printed context around a blank, per direction.

    All three directions matter and none alone is enough: field labels sit to
    the left, checkbox labels sit to the *right* of their tick box, and a table
    cell's meaning comes from the column header above it (its left neighbour is
    just the row number). They are kept separate rather than concatenated
    because matching must prefer the immediate left label — a box directly
    under a row of tick boxes otherwise inherits their text.
    """
    narrow = (rect[2] - rect[0]) < _NARROW_PT
    left = _clean_label(_label_left(words, rect))
    return {
        "left": left,
        "above": _clean_label(_label_above(words, rect)),
        "right": _clean_label(_label_right(words, rect)) if (narrow or not left) else '',
        # For a tick box, the option it stands for — the nearer of the two sides.
        "check": _checkbox_label(words, rect) if narrow else '',
    }


def analyze(pdf_bytes):
    """Rasterise each page and locate every fillable blank.

    Returns {pages: [{index, width, height, image, candidates:[...]}]}, where
    each candidate is {id, page, x0, y0, x1, y1, kind, label}. Coordinates are
    PDF points; `image` is a base64 PNG at PREVIEW_ZOOM scale.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages = []
        next_id = 1
        for pno in range(doc.page_count):
            page = doc[pno]
            words = page.get_text("words")
            headings = _section_headings(words)
            try:
                raw = page.get_text("rawdict")   # shared by two detectors
            except Exception:
                raw = None

            found = (_detect_drawn_boxes(page)
                     + _detect_underscore_runs(page, raw)
                     + _detect_grid_cells(page))

            # De-duplicate: an earlier detector wins, so a drawn box is never
            # also reported as a grid cell.
            kept = []
            for rect, kind in found:
                if rect[2] - rect[0] < 18 or rect[3] - rect[1] < 6:
                    continue
                if any(_rects_overlap(rect, k[0]) for k in kept):
                    continue
                kept.append((rect, kind))

            kept.sort(key=lambda k: (k[0][1], k[0][0]))
            kept = kept[:_MAX_CANDIDATES_PER_PAGE]

            # What printed text already sits inside each candidate. This is the
            # strongest available signal: a ruled table's label column is a cell
            # full of text, and the empty cell beside it is where we write.
            annotated = [(rect, kind, _inner_words(words, rect)) for rect, kind in kept]

            candidates = []
            for rect, kind, inner in annotated:
                if inner:
                    if kind == 'cell':
                        # Pre-printed content (a label column, or the supplier's
                        # own reference details) — not a blank. Skip it; it is
                        # still used below to label its empty row neighbour.
                        continue
                    # A drawn box with a label printed inside it, e.g. UB's
                    # "Value AED." — the directive's rule is to write after the
                    # label, so start the fillable region at the label's right edge.
                    rect = [round(inner[1], 1), rect[1], rect[2], rect[3]]
                    if rect[2] - rect[0] < 18:
                        continue

                parts = _nearest_label(words, rect)
                row = _clean_label(_row_label(annotated, rect))
                if row:
                    # A ruled table's own label column beats anything inferred
                    # from loose neighbouring words.
                    parts = {**parts, "left": row}

                candidates.append({
                    "id": next_id,
                    "page": pno,
                    "x0": rect[0], "y0": rect[1], "x1": rect[2], "y1": rect[3],
                    "kind": kind,
                    # The tick-box option leads, so both the user and the model
                    # see which side the box actually belongs to.
                    "label": ' | '.join(dict.fromkeys(
                        p for p in (parts['check'], parts['left'],
                                    parts['above'], parts['right']) if p))[:110],
                    "label_parts": parts,
                    "section": _section_for(headings, rect),
                })
                next_id += 1

            paragraphs = []
            for para in _detect_paragraphs(page, raw):
                paragraphs.append({"id": next_id, "page": pno, **para})
                next_id += 1

            pix = page.get_pixmap(matrix=fitz.Matrix(PREVIEW_ZOOM, PREVIEW_ZOOM))
            pages.append({
                "index": pno,
                "paragraphs": paragraphs,
                "width": round(page.rect.width, 1),
                "height": round(page.rect.height, 1),
                "image": "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode(),
                "candidates": candidates,
            })
        return {"pages": pages, "zoom": PREVIEW_ZOOM}
    finally:
        doc.close()


# ============================================================================
# AI FIELD MAPPING
# ============================================================================

_RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 529}
_MODEL = "claude-opus-4-6"
_FALLBACK_MODEL = "claude-sonnet-5"


def _claude(prompt, max_tokens=8000, max_attempts=3):
    """Claude call with backoff then Sonnet fallback — same shape as
    ai_extraction._create_with_retry, which this feature's 529s taught us to use."""
    import anthropic

    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in environment")
    client = anthropic.Anthropic(api_key=api_key)

    last_err = None
    for model in (_MODEL, _FALLBACK_MODEL):
        for attempt in range(max_attempts):
            try:
                msg = client.messages.create(
                    model=model, max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}])
                return msg.content[0].text
            except anthropic.APIStatusError as e:
                last_err = e
                if getattr(e, 'status_code', None) not in _RETRYABLE:
                    raise
            except anthropic.APIConnectionError as e:
                last_err = e
            if attempt < max_attempts - 1:
                time.sleep(min(2 ** attempt, 8))
    raise last_err


# Ordered label patterns -> field key. First match wins, so the specific rules
# come first. Deliberately conservative: an ambiguous label (a bare "Name" that
# could be a partner, a contact or a reference) is left for the AI pass or for
# the user, because a confident wrong guess costs more than a blank.
_HEURISTICS = [
    (r'vat|trn|tax\s*reg',                         'vat_trn'),
    (r'company\s*name|name\s*of\s*(the\s*)?(company|firm|establishment)|applicant', 'company_name'),
    (r'trade\s*licen[cs]e|t\.?\s*l\.?\s*(no|number)|licen[cs]e\s*(no|number)', 'trade_license_no'),
    (r'expiry|valid\s*until|valid\s*till',         'tl_expiry'),
    (r'p\.?\s*o\.?\s*box|post\s*box',              'po_box'),
    (r'nature\s*of\s*business|business\s*activity|main\s*activity', 'nature_of_business'),
    (r'year\s*of\s*establish|date\s*of\s*establish|established|establishment', 'year_established'),
    (r'e-?\s*mail',                                'email'),
    (r'web\s*site|website|url',                    'website'),
    (r'iban',                                      'bank_iban'),
    (r'account\s*(no|number)|a\s*/\s*c\s*(no)?',   'bank_account_no'),
    (r'branch',                                    'bank_branch'),
    (r'bank\s*name|banker|name\s*of\s*bank',       'bank_name'),
    (r'passport',                                  'passport_no'),
    (r'nationality',                               'director_nationality'),
    (r'position|designation',                      'director_position'),
    (r'manager\s*name|managing|director|owner|proprietor|partner\s*name', 'director_name'),
    (r'registered\s*address|address',              'address'),
    (r'street',                                    'address_short'),
    (r'city|emirate',                              'city'),
    (r'building',                                  'building'),
    (r'mobile|mob\.?\s*(no)?',                     'director_mobile'),
    (r'tele?phone|tel\.?\s*(no)?|phone|contact\s*no', 'tel'),
    (r'credit\s*(limit|amount)|amount\s*required|limit\s*required', 'credit_amount'),
    (r'credit\s*(period|term)|payment\s*(period|term)', 'credit_term_days'),
]
_HEURISTICS = [(re.compile(p, re.I), k) for p, k in _HEURISTICS]

# Labels that mean "don't fill this" regardless of what else they match.
_SKIP = re.compile(r'\bfax\b|%\s*of\s*share|signature|stamp|for\s*office\s*use|form\s*no', re.I)

# A label carrying an actual value — a long number, a URL, an email — is the
# supplier's own pre-printed letterhead ("P.O. Box: 22244 Ajman"), not a blank
# for us. Two-digit runs are fine: "60 Days" is a real checkbox label.
_PREPRINTED = re.compile(r'\d{4,}|www\.|@')

# Tick boxes are too narrow for any value; the only thing that belongs in one
# is an X, and only when the label beside it describes us.
_CHECKBOX_RULES = [
    (re.compile(r'^LLC\b', re.I),        'company_type'),
    (re.compile(r'^90\s*days', re.I),    'credit_term_days'),
    (re.compile(r'^PDC\b', re.I),        'payment_terms'),
]
_NARROW_PT = 30

# Sections that ask about somebody else — a sponsor, an associate company, a
# trade reference. The labels there ("Tel. No", "P.O. Box") look exactly like
# ours, so a match inside one is kept but left switched off for the user to
# confirm rather than silently writing our details into their box.
_OTHER_PARTY = re.compile(
    r'sponsor|associate|reference|guarantor|group\s*compan|subsidiar|sister\s*concern', re.I)


def heuristic_mapping(analysis):
    """Deterministic label -> field matching. No API call, no cost, no latency.

    Covers the common fields that nearly every credit form asks for; the AI
    pass only has to deal with what this leaves behind.
    """
    out = []
    for page in analysis['pages']:
        for c in page['candidates']:
            parts = c.get('label_parts') or {}
            label = c.get('label') or ''
            if not label or _SKIP.search(label) or _PREPRINTED.search(label):
                continue

            if (c['x1'] - c['x0']) < _NARROW_PT:
                # A tick box belongs to the option nearest it, either side.
                for pattern, key in _CHECKBOX_RULES:
                    if pattern.match(parts.get('check') or ''):
                        out.append({"id": c['id'], "field_key": key, "kind": "checkbox"})
                        break
                continue

            # Left label first: it is the field's own name. Only fall back to
            # the column header above when there is no left label to go on.
            matched = None
            for segment in (parts.get('left'), parts.get('above')):
                if not segment:
                    continue
                for pattern, key in _HEURISTICS:
                    if pattern.search(segment):
                        matched = key
                        break
                if matched:
                    break

            if matched:
                # A narrow box can't hold the full address.
                if matched == 'address' and (c['x1'] - c['x0']) < 200:
                    matched = 'address_short'
                out.append({"id": c['id'], "field_key": matched, "kind": "text",
                            "weak": bool(_OTHER_PARTY.search(c.get('section') or ''))})
    return out


def propose_mapping(analysis):
    """Ask Claude which profile field belongs in each detected blank.

    The model only *matches* labels to field keys — it never emits coordinates,
    which stay exactly as the detectors measured them.
    """
    catalogue = "\n".join(f"- {f['key']}: {f['label']} (e.g. {f['value']})"
                          for f in company_profile())

    compact = []
    for page in analysis['pages']:
        for c in page['candidates']:
            compact.append({"id": c['id'], "pg": c['page'], "label": c['label'],
                            "section": c.get('section', ''),
                            "w": round(c['x1'] - c['x0'])})
    if not compact:
        return []

    prompt = (
        "You are mapping blanks in a supplier CREDIT APPLICATION FORM to fields of "
        "the applicant company (Newell Electromechanical Works, a Dubai MEP contractor).\n\n"
        "Each blank below was detected geometrically and carries the printed text "
        "found immediately to its left or above it, the form section it sits in, "
        "and its width in points.\n\n"
        "AVAILABLE FIELD KEYS:\n" + catalogue + "\n\n"
        "BLANKS:\n" + json.dumps(compact) + "\n\n"
        "Return ONLY JSON: {\"placements\":[{\"id\":<blank id>,\"field_key\":\"<key>\","
        "\"kind\":\"text\"|\"checkbox\"}]}\n\n"
        "Rules:\n"
        "- Omit any blank you are not confident about. A wrong guess is worse than "
        "leaving it for the user; unmapped blanks are shown for manual assignment.\n"
        "- Omit blanks that are the supplier's own pre-printed reference details "
        "(e.g. the supplier's own bank accounts) rather than something we fill in.\n"
        "- Watch the section: one that asks about a local sponsor, an associate or "
        "sister company, or a trade reference is asking about somebody else, and "
        "its 'Tel. No' or 'P.O. Box' is NOT ours. Omit those.\n"
        "- Use kind \"checkbox\" for a small tick box whose printed label matches us "
        "(company type LLC, a 90-days credit term, PDC payment terms). For those, "
        "field_key should still name the field the box represents.\n"
        "- Prefer address_short over address when the blank is under ~200 points wide.\n"
        "- Put bank_account_no into an IBAN blank: we have no IBAN on file.\n"
        "- Each field key may be used more than once if the form repeats it."
    )

    text = _claude(prompt)
    start, end = text.find('{'), text.rfind('}') + 1
    if start == -1 or end <= start:
        raise ValueError("No JSON in model response")
    return json.loads(text[start:end]).get('placements', [])


def map_fields(analysis, use_ai=True):
    """Full mapping pass: deterministic rules first, AI for the remainder.

    Returns (mapping, ai_status). The AI is strictly additive — if it is
    unavailable (no key, overloaded) the heuristic result still stands and the
    user assigns the rest by hand, so the page never hard-fails on a bad key.
    """
    mapping = heuristic_mapping(analysis)
    if not use_ai:
        return mapping, 'skipped'

    done = {m['id'] for m in mapping}
    remaining = {
        "pages": [{**p, "candidates": [c for c in p['candidates'] if c['id'] not in done]}
                  for p in analysis['pages']]
    }
    if not any(p['candidates'] for p in remaining['pages']):
        return mapping, 'not_needed'

    try:
        extra = propose_mapping(remaining)
    except Exception as e:
        return mapping, f'unavailable: {type(e).__name__}'

    # Where the rules already placed a field on a page, they win: they are the
    # deterministic path and have been checked against the real forms. Without
    # this the model's second guess at the same field can outrank them purely
    # by sitting higher up the page — it ticked "60Days" over the rules' correct
    # "90days" that way.
    page_of = {c['id']: c['page'] for p in analysis['pages'] for c in p['candidates']}
    claimed = {(page_of.get(m['id']), m['field_key']) for m in mapping}

    for m in extra:
        if not (isinstance(m, dict) and m.get('field_key') and m.get('id') not in done):
            continue
        slot = (page_of.get(m['id']), m['field_key'])
        if slot in claimed:
            continue
        mapping.append(m)
        claimed.add(slot)
        done.add(m['id'])
    return mapping, 'ok'


def build_placements(analysis, mapping=None, values=None):
    """Turn detected candidates + a field mapping into ready-to-edit placements.

    Every candidate becomes a placement so the user can see and use it; the
    unmapped ones simply arrive with no field_key and no text.
    """
    values = values or profile_values()
    by_id = {m['id']: m for m in (mapping or []) if isinstance(m, dict) and 'id' in m}

    out = []
    for page in analysis['pages']:
        # Forms repeat a field down a table (four "Designation" signatory rows,
        # three reference blocks). Only the first row per page is switched on by
        # default — the rest stay visible so the user can tick them if needed.
        seen_on_page = set()
        for c in page['candidates']:
            m = by_id.get(c['id'], {})
            key = m.get('field_key') or None
            kind = m.get('kind') if m.get('kind') in ('text', 'checkbox', 'multiline') else 'text'
            text = "X" if kind == 'checkbox' else (values.get(key, '') if key else '')
            first = bool(key) and key not in seen_on_page and not m.get('weak')
            if key:
                seen_on_page.add(key)
            out.append({
                "id": c['id'],
                "page": c['page'],
                "x0": c['x0'], "y0": c['y0'], "x1": c['x1'], "y1": c['y1'],
                "field_key": key,
                "kind": kind,
                "text": text,
                "size": None,
                "label": c['label'],
                "enabled": first,
            })
    return out

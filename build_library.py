"""Build the Newell Assistant's library of official UAE documents.

Steps (each re-runnable; files are cached in .tmp/library/):
  crawl     walk the official listing pages -> .tmp/library/catalog.json
  download  fetch each listed PDF -> .tmp/library/pdf/ (skips ones already there)
  ingest    extract text (PyMuPDF), split by article / section, upsert into
            library_documents + library_chunks (unchanged documents are skipped)
  stats     counts per category / type in Supabase
  baseline  record each stored PDF's size / ETag / Last-Modified (HEAD only), so
            the automatic updater (assistant_updater.py) can spot changed files
            without downloading them

    cd execution
    python build_library.py crawl
    python build_library.py download
    python build_library.py ingest --dry-run     # show what would be stored
    python build_library.py ingest
    python build_library.py all                  # crawl + download + ingest
    python build_library.py add <pdf_url> --title "..." --category Labour --type law
                                                 # one document not on a listing page

Read directives/assistant_library.md first. Tables: migrate_assistant_library.py.
The scheduled self-update (assistant_updater.py) uses crawl_catalog(), head_info(),
fetch(), prepare() and store() from here. This file lives in bpv-backend (it
is deployed, for the updater) and is copied to execution/ like the others.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
# On Railway the app folder isn't meant for scratch files: use the temp dir.
WORK = Path(os.getenv("LIBRARY_WORK_DIR") or (
    Path(tempfile.gettempdir()) / "library" if os.getenv("RAILWAY_ENVIRONMENT") else ROOT / ".tmp" / "library"))
PDF_DIR = WORK / "pdf"
CATALOG = WORK / "catalog.json"
EXTRA = Path(__file__).resolve().parent / "library_extra.json"  # documents added with `add`

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
DELAY = 0.7  # seconds between requests to the same site

LISTINGS = [
    # (key, url, category when the item has no tag)
    ("legislation", "https://tax.gov.ae/en/legislation.aspx", ""),
    ("vat_guides", "https://tax.gov.ae/en/taxes/Vat/guides.references.aspx", "VAT"),
    ("ct_guides", "https://tax.gov.ae/en/taxes/corporate.tax/corporate.tax.guides.references.aspx", "Corporate Tax"),
]
# Not relevant to an MEP contractor; left out to keep search results on point.
SKIP_CATEGORIES = {"Excise Tax", "Economic Substance Regulations"}

CHUNK_TARGET = 2500
CHUNK_MAX = 4000

session = requests.Session()
session.headers["User-Agent"] = UA


# ---------------------------------------------------------------------------
# crawl
# ---------------------------------------------------------------------------

def _form_fields(soup):
    data = {}
    for inp in soup.select("form input"):
        name = inp.get("name")
        if name and inp.get("type") not in ("submit", "image", "button", "checkbox", "radio"):
            data[name] = inp.get("value", "")
    for sel in soup.select("form select"):
        opt = sel.select_one("option[selected]") or sel.select_one("option")
        if sel.get("name"):
            data[sel["name"]] = opt.get("value", "") if opt else ""
    return data


def _date(text):
    text = (text or "").strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _items(soup, listing, default_category):
    out = []
    for links in soup.select("div.downloadlinks"):
        row = links
        while row is not None and not (row.name == "div" and "row" in (row.get("class") or [])
                                       and row.find("div", class_="col-md-7", recursive=False)):
            row = row.parent
        if row is None:
            continue
        left = row.find("div", class_="col-md-7", recursive=False)
        title_el = left.find("div", class_="d-flex")
        # The title is the div's own text; the "New" badge is a child div.
        title = " ".join(t.strip() for t in title_el.find_all(string=True, recursive=False)).strip() if title_el else ""
        dates = {}
        for cap in left.select("span.lastmodifiedDateCaption"):
            val = cap.find_next_sibling("span", class_="lastmodifiedDate")
            if val:
                dates[cap.get_text(strip=True).rstrip(" :").lower()] = _date(val.get_text())
        tag = left.select_one("span.tag_category")
        tag = tag.get_text(strip=True) if tag else ""
        # Plain PDFs, plus EmaraTax manuals served as DownloadOpenTextFile?fileUrl=...pdf
        pdfs = [a["href"] for a in links.select("a[href]")
                if ".pdf" in a["href"].lower() and "youtube" not in a["href"].lower()]
        if not pdfs or "archive" in tag.lower():
            continue  # archive = superseded versions
        title = " ".join(title.split())
        if listing == "legislation":
            category = CATEGORY_NAMES.get(tag, tag or default_category)
        else:
            category = default_category  # on guide pages the tag is the kind of document
        out.append({
            "listing": listing,
            "source": urlsplit(pdfs[0]).netloc,
            "url": pdfs[0],
            "title": title,
            "tag": tag,
            "category": category,
            "doc_type": doc_type(title, listing, tag, pdfs[0]),
            "issue_date": dates.get("issue date"),
            "publish_date": dates.get("publish date"),
        })
    return out


CATEGORY_NAMES = {"Federal Tax Procedures": "Tax Procedures", "Federal Tax Authority": "Tax Procedures"}


def _get_soup(sess, url, data=None):
    r = sess.post(url, data=data, timeout=60) if data else sess.get(url, timeout=60)
    r.encoding = "utf-8"  # the pages are UTF-8 but don't always say so
    return BeautifulSoup(r.text, "html.parser")


def crawl_listing(key, url, default_category):
    # A fresh session per listing: ASP.NET keeps pager state per session, and a
    # session reused across listings returned empty pages.
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    soup = _get_soup(sess, url)
    items, page = [], 1
    while True:
        found = _items(soup, key, default_category)
        items.extend(found)
        html = str(soup)
        m = re.search(r'"_pageCount":(\d+)', html)
        pages = int(m.group(1)) if m else 1
        print(f"  {key}: page {page}/{pages}, {len(found)} items")
        nxt = re.search(r"__doPostBack\(&#39;([^&]+LinkButtonNext)&#39;|__doPostBack\('([^']+LinkButtonNext)'", html)
        if page >= pages or not nxt:
            break
        data = _form_fields(soup)
        data["__EVENTTARGET"] = nxt.group(1) or nxt.group(2)
        data["__EVENTARGUMENT"] = ""
        time.sleep(DELAY)
        soup = _get_soup(sess, url, data)
        page += 1
    return items


def crawl_catalog():
    """All listing pages -> (documents, {listing: item count}). Raises if a listing fails."""
    catalog, seen, counts = [], set(), {}
    for key, url, default_category in LISTINGS:
        items = crawl_listing(key, url, default_category)
        counts[key] = len(items)
        for item in items:
            if item["url"] in seen or item["category"] in SKIP_CATEGORIES:
                continue
            seen.add(item["url"])
            catalog.append(item)
    return catalog, counts


def crawl():
    WORK.mkdir(parents=True, exist_ok=True)
    catalog, _ = crawl_catalog()
    CATALOG.write_text(json.dumps(catalog, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Catalog: {len(catalog)} documents -> {CATALOG}")
    for (c, t), n in sorted(Counter((i["category"], i["doc_type"]) for i in catalog).items()):
        print(f"  {c or '-'} / {t}: {n}")


def doc_type(title, listing, tag, url):
    """Decided by how the title starts: a ministerial decision 'for the purposes of
    Federal Decree-Law No. 47' is a decision, not the law."""
    t = title.lower()
    if "executive regulation" in t:
        return "regulation"
    if t.startswith(("federal decree-law", "federal decree law", "federal law")):
        return "law"
    if t.startswith(("cabinet decision", "cabinet resolution")):
        return "cabinet_decision"
    if t.startswith("ministerial decision"):
        return "ministerial_decision"
    if t.startswith(("fta decision", "federal tax authority decision", "decision no")):
        return "fta_decision"
    if t.startswith("directive"):
        return "directive"
    if "downloadopentextfile" in url.lower() or "procedure" in tag.lower():
        return "emaratax_manual"
    if tag == "Public Clarifications" or re.match(r"(vatp|ctp|taxp)\s*\d", t):
        return "public_clarification"
    if listing != "legislation":
        return "guide"
    return "other"


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------

def _pdf_url(d):
    """Where to download from. A manual entry may point its link at a page of a
    shared PDF (url "...pdf#page=24") and download the whole file from pdf_url."""
    return d.get("pdf_url") or d["url"].split("#")[0]


def _pdf_path(d):
    return PDF_DIR / (hashlib.sha1(_pdf_url(d).encode()).hexdigest()[:16] + ".pdf")


def _encoded(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, quote(parts.path.replace("//", "/"), safe="/%()"), parts.query, ""))


def head_info(d):
    """{size, etag, last_modified} from a HEAD request, or None when the server
    doesn't describe the file (EmaraTax DownloadOpenTextFile links answer with a
    tiny HTML stub)."""
    r = session.head(_encoded(_pdf_url(d)), timeout=60, allow_redirects=True)
    size = int(r.headers.get("Content-Length") or 0)
    if r.status_code != 200 or size < 1000:
        return None
    return {"size": size, "etag": r.headers.get("ETag"), "last_modified": r.headers.get("Last-Modified")}


def fetch(d):
    """Download one document's PDF. Returns (bytes, file info)."""
    r = session.get(_encoded(_pdf_url(d)), timeout=180)
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        raise ValueError(f"HTTP {r.status_code}, {r.headers.get('content-type')}")
    return r.content, {"size": len(r.content), "etag": r.headers.get("ETag"),
                       "last_modified": r.headers.get("Last-Modified")}


def extra_docs():
    """Documents registered by hand with `add` (not on a crawled listing)."""
    return json.loads(EXTRA.read_text(encoding="utf-8")) if EXTRA.exists() else []


def _all_docs():
    docs = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else []
    if EXTRA.exists():
        docs += json.loads(EXTRA.read_text(encoding="utf-8"))
    return docs


def download(limit=None):
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    docs = _all_docs()[:limit] if limit else _all_docs()
    got = skipped = failed = 0
    for i, d in enumerate(docs, 1):
        path = _pdf_path(d)
        if path.exists() and path.stat().st_size > 0:
            skipped += 1
            continue
        try:
            r = session.get(_encoded(_pdf_url(d)), timeout=120)
            if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                raise ValueError(f"HTTP {r.status_code}, {r.headers.get('content-type')}")
            path.write_bytes(r.content)
            got += 1
            print(f"  [{i}/{len(docs)}] {len(r.content) // 1024} KB  {d['title'][:80]}")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(docs)}] FAILED {d['title'][:70]}: {e}")
        time.sleep(DELAY)
    print(f"Downloaded {got}, already had {skipped}, failed {failed}")


# ---------------------------------------------------------------------------
# text + chunking
# ---------------------------------------------------------------------------

ARTICLE_RE = re.compile(r"^\s*(?:Article|Art\.)\s*\(?\s*(\d{1,3})\s*\)?\s*[-–:.]?\s*(.*)$", re.I)
# "Article (55) of the VAT Executive Regulation provides ..." is a reference, not a heading.
ARTICLE_REFERENCE = re.compile(r"^(\(\d+\)\s*)?(of|and|or|to|in|from|shall|as|above|below|herein|thereof|,)\b", re.I)


# Footnote markers glued to an article title: "Tax invoices 43,44", "Special Cases13".
FOOTNOTE_REF = re.compile(r"(?<=[A-Za-z)])\s*\d{1,3}(\s*,\s*\d{1,3})*$")


def _article_heading(line):
    m = ARTICLE_RE.match(line)
    if not m:
        return None
    title = m.group(2).strip()
    if title and (ARTICLE_REFERENCE.match(title) or title[0].islower() or len(title) > 100):
        return None
    return m
SECTION_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+([A-Z][^.]{2,90})$")
ARABIC = re.compile(r"[؀-ۿ]")


# Some PDFs (MoHRE's Labour Law) lose the "Th" ligature: "Te Minister", "Tis Resolution".
TH_WORDS = re.compile(r"\bT(e|is|ey|ere|ese|ose|at|en|us|ird|ursday|irty|rough|ree|ereof|erefore|ereby|erein)\b")


def _two_column_text(page):
    """Text of a two-column page: left column top to bottom, then the right one.

    The PDF's own text order interleaves the columns on some pages (MoHRE's
    Labour Law put Article 51's body after Article 54). Blocks that span the
    middle (headings, full-width paragraphs) count as left-column.
    """
    mid = page.rect.width / 2
    blocks = [b for b in page.get_text("blocks") if b[6] == 0]  # text blocks only
    def key(b):
        x0, y0, x1 = b[0], b[1], b[2]
        spans_middle = x0 < mid - 30 and x1 > mid + 30
        return (0 if spans_middle or x0 < mid else 1, y0, x0)
    return "\n".join(b[4] for b in sorted(blocks, key=key))


def page_lines(source, page_from=None, page_to=None, fix_th=False, two_columns=False):
    """[(page_no, line)] with running headers/footers removed.

    page_from/page_to (1-based, inclusive) keep only part of a PDF that holds
    more than one document. two_columns reads each page column by column (only
    for documents known to be laid out that way; it would scramble tables).
    """
    import fitz
    pdf = fitz.open(stream=source, filetype="pdf") if isinstance(source, (bytes, bytearray)) else fitz.open(source)
    with pdf:
        pages = [_two_column_text(p) if two_columns else p.get_text("text") for p in pdf]
    first = (page_from or 1) - 1
    pages = pages[first:page_to or len(pages)]
    # NFKC turns ligature glyphs ("ﬁ", "ﬂ", "ﬃ") back into letters, so "Deﬁnitions" is searchable.
    pages = [unicodedata.normalize("NFKC", t) for t in pages]
    if fix_th:
        pages = [TH_WORDS.sub(r"Th\1", t) for t in pages]
    per_page = [[" ".join(l.split()) for l in t.splitlines()] for t in pages]
    per_page = [[l for l in lines if l] for lines in per_page]
    if len(per_page) >= 4:
        freq = Counter(l for lines in per_page for l in set(lines) if len(l) < 120)
        common = {l for l, n in freq.items() if n > 0.5 * len(per_page)}
        # bare page numbers ("12", "Page 12 of 40") are noise too
        per_page = [[l for l in lines if l not in common and not re.fullmatch(r"(page\s*)?\d+(\s*(of|/)\s*\d+)?", l, re.I)]
                    for lines in per_page]
    return [(first + i + 1, l) for i, lines in enumerate(per_page) for l in lines], len(pages)


def _pack(segments):
    """Merge/split (heading, [(page, line)]) segments into chunks of a sensible size."""
    chunks = []
    for heading, lines in segments:
        buf, size, part = [], 0, 1
        for page, line in lines:
            buf.append((page, line))
            size += len(line) + 1
            # Cut long segments at a sentence end past the target, or hard at the max.
            if size >= CHUNK_MAX or (size >= CHUNK_TARGET and line.rstrip().endswith((".", ":", ";"))):
                chunks.append((heading if part == 1 else f"{heading} (part {part})", buf))
                buf, size, part = [], 0, part + 1
        if buf:
            chunks.append((heading if part == 1 else f"{heading} (part {part})", buf))
    out = []
    for heading, buf in chunks:
        text = "\n".join(l for _, l in buf).strip()
        if len(text) >= 40:
            out.append({"heading": heading[:200], "page_from": buf[0][0], "page_to": buf[-1][0], "text": text})
    return out


def chunk_document(lines):
    """Split by article for laws/regulations/decisions, else by numbered section."""
    articles = [i for i, (_, l) in enumerate(lines) if _article_heading(l)]
    if len(articles) >= 3:
        # MoHRE's layout marks titles "►Title". PyMuPDF puts that line just below
        # the "Article (n)" line or just above it depending on the page layout:
        # prefer below, else take the one above (and start the segment there).
        def marked(i):
            return 0 <= i < len(lines) and lines[i][1].startswith("►")
        starts = [i if marked(i + 1) or not marked(i - 1) else i - 1 for i in articles]
        segments = [("Preamble", lines[:starts[0]])] if starts[0] else []
        for n, (start, art) in enumerate(zip(starts, articles)):
            end = starts[n + 1] if n + 1 < len(starts) else len(lines)
            m = _article_heading(lines[art][1])
            heading = f"Article ({m.group(1)})"
            title = m.group(2).strip()
            if start < art:
                title = lines[start][1]
            elif not title and art + 1 < end and len(lines[art + 1][1]) < 100:
                title = lines[art + 1][1]
            title = title.lstrip("► ").strip()
            body_lines = [l for _, l in lines[art + 1:end] if l.lstrip("► ").strip() != title]
            # A table-of-contents entry is a run of short titles: no sentence
            # punctuation anywhere. A real article always has some.
            if sum(len(l) for l in body_lines) < 80 or not any(l.rstrip().endswith((".", ":", ";")) for l in body_lines):
                continue
            title = FOOTNOTE_REF.sub("", title).strip()  # "Date of Supply13" -> "Date of Supply"
            segments.append((f"{heading} {title}".strip(), lines[start:end]))
        # The last table-of-contents entry swallows the preamble that follows it;
        # if its article number comes again later, that earlier copy is the preamble.
        number = lambda h: h.split(")")[0] if h.startswith("Article (") else None
        later = Counter(number(h) for h, _ in segments)
        renamed = []
        for h, seg in segments:
            n = number(h)
            later[n] -= 1
            renamed.append(("Preamble" if n and later[n] > 0 else h, seg))
        return _pack(renamed)
    segments, heading, buf = [], "Introduction", []
    for page, line in lines:
        m = SECTION_RE.match(line)
        if m and buf:
            segments.append((heading, buf))
            heading, buf = f"{m.group(1)} {m.group(2)}", []
        elif m:
            heading = f"{m.group(1)} {m.group(2)}"
        buf.append((page, line))
    if buf:
        segments.append((heading, buf))
    return _pack(segments)


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def ingest(dry_run=False, limit=None, force=False):
    docs = _all_docs()[:limit] if limit else _all_docs()
    if not dry_run:
        import db
        existing = {r["url"]: r["content_hash"] for r in db.q("SELECT url, content_hash FROM library_documents")}
    else:
        existing = {}
    stored = unchanged = missing = empty = 0
    for d in docs:
        path = _pdf_path(d)
        if not path.exists():
            missing += 1
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if existing.get(d["url"]) == digest and not force:
            unchanged += 1
            continue
        try:
            d, digest, pages, chars, chunks = prepare(d, data)
        except ValueError as e:
            print(f"  SKIPPED {d['title'][:70]}: {e}")
            empty += 1
            continue
        arabic = len(ARABIC.findall("".join(c["text"] for c in chunks[:5]))) > 0.3 * min(chars, 10000)
        if dry_run:
            print(f"  {len(chunks):4d} chunks {chars:8,d} chars  p{pages:<4d} {'AR ' if arabic else ''}{d['doc_type']:<20} {d['title'][:70]}")
            print(f"       first: {chunks[0]['heading'][:70]!r}  last: {chunks[-1]['heading'][:70]!r}")
            stored += 1
            continue
        # A --force re-chunk of an unchanged file is not news; a changed file is.
        store(d, digest, pages, chars, chunks, {"size": len(data)}, needs_review=existing.get(d["url"]) != digest)
        stored += 1
        print(f"  stored {len(chunks):4d} chunks  {d['title'][:80]}")
    print(f"{'Would store' if dry_run else 'Stored'} {stored}, unchanged {unchanged}, not downloaded {missing}, no text {empty}")


def prepare(d, data):
    """PDF bytes -> (doc, sha256, pages, chars, chunks). Raises ValueError if unusable."""
    digest = hashlib.sha256(data).hexdigest()
    try:
        lines, pages = page_lines(data, d.get("page_from"), d.get("page_to"), d.get("fix_th"), d.get("two_columns"))
    except Exception as e:
        raise ValueError(f"unreadable PDF ({e})")
    chunks = chunk_document(lines)
    chars = sum(len(c["text"]) for c in chunks)
    if chars < 200:
        raise ValueError("no text layer (scanned?)")
    if d["doc_type"] == "emaratax_manual" and chars / max(pages, 1) > 1000:
        # Real FTA guides (e.g. the Real Estate Guide) also come through
        # DownloadOpenTextFile; screen manuals are mostly screenshots (~300-500 chars/page).
        d = {**d, "doc_type": "guide"}
    return d, digest, pages, chars, chunks


def store(d, digest, pages, chars, chunks, file_info=None, needs_review=True):
    """Upsert one document and replace its chunks. Returns the document id.

    needs_review puts it in the self-update review queue (reviewed_at = NULL):
    the assistant will read it against its knowledge base. EmaraTax screen
    manuals never need it.
    """
    import db
    from psycopg2.extras import execute_values
    info = file_info or {}
    needs_review = needs_review and d["doc_type"] != "emaratax_manual"

    def write(conn):
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO library_documents (source, listing, url, title, category, doc_type, issue_date,
                                               publish_date, pages, chars, content_hash, file_size, etag,
                                               last_modified, active, reviewed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true,
                        CASE WHEN %s THEN NULL ELSE now() END)
                ON CONFLICT (url) DO UPDATE SET
                  source = EXCLUDED.source, listing = EXCLUDED.listing, title = EXCLUDED.title,
                  category = EXCLUDED.category, doc_type = EXCLUDED.doc_type, issue_date = EXCLUDED.issue_date,
                  publish_date = EXCLUDED.publish_date, pages = EXCLUDED.pages, chars = EXCLUDED.chars,
                  content_hash = EXCLUDED.content_hash,
                  file_size = COALESCE(EXCLUDED.file_size, library_documents.file_size),
                  etag = COALESCE(EXCLUDED.etag, library_documents.etag),
                  last_modified = COALESCE(EXCLUDED.last_modified, library_documents.last_modified),
                  active = true, fetched_at = now(), updated_at = now(),
                  reviewed_at = CASE WHEN %s THEN NULL ELSE library_documents.reviewed_at END
                RETURNING id""",
                (d["source"], d["listing"], d["url"], d["title"], d["category"], d["doc_type"],
                 d.get("issue_date"), d.get("publish_date"), pages, chars, digest,
                 info.get("size"), info.get("etag"), info.get("last_modified"), needs_review, needs_review))
            doc_id = cur.fetchone()[0]
            cur.execute("DELETE FROM library_chunks WHERE document_id = %s", (doc_id,))
            execute_values(cur, """
                INSERT INTO library_chunks (document_id, seq, title, heading, page_from, page_to, text)
                VALUES %s""",
                [(doc_id, i, d["title"], c["heading"], c["page_from"], c["page_to"], c["text"])
                 for i, c in enumerate(chunks)])
            return doc_id

    return db.transaction(write)


def baseline():
    """Record HEAD file info for every stored document (no downloads, no re-chunking)."""
    import db
    rows = db.q("SELECT id, url FROM library_documents")
    docs = {d["url"]: d for d in _all_docs()}
    done = described = 0
    for r in rows:
        d = docs.get(r["url"]) or {"url": r["url"]}
        try:
            info = head_info(d)
        except Exception as e:
            print(f"  HEAD failed {r['url'][:80]}: {e}")
            continue
        done += 1
        if info:
            described += 1
            db.execute("UPDATE library_documents SET file_size = %s, etag = %s, last_modified = %s WHERE id = %s",
                       (info["size"], info["etag"], info["last_modified"], r["id"]))
        time.sleep(DELAY / 2)
    print(f"Checked {done} documents; {described} had size/ETag headers (the rest are re-checked by hash)")


def add(url, title, category, dtype, issue_date=None, pdf_url=None, pages=None, fix_th=False, two_columns=False):
    """Register one document that isn't on a crawled listing page (e.g. the Labour Law).

    pages "24-38" keeps only those pages of the PDF (when one file holds two
    documents); give each part its own url, e.g. "<pdf>#page=24", and the
    shared file as pdf_url.
    """
    extra = json.loads(EXTRA.read_text(encoding="utf-8")) if EXTRA.exists() else []
    extra = [e for e in extra if e["url"] != url]
    entry = {"listing": "manual", "source": urlsplit(url).netloc, "url": url, "title": title,
             "category": category, "doc_type": dtype, "issue_date": issue_date, "publish_date": None}
    if pdf_url:
        entry["pdf_url"] = pdf_url
    if pages:
        first, _, last = pages.partition("-")
        entry["page_from"], entry["page_to"] = int(first), int(last or first)
    if fix_th:
        entry["fix_th"] = True
    if two_columns:
        entry["two_columns"] = True
    extra.append(entry)
    EXTRA.write_text(json.dumps(extra, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Added to {EXTRA.name}. Now run: download, then ingest")


def stats():
    import db
    for r in db.q("""SELECT d.category, d.doc_type, count(DISTINCT d.id) AS docs, count(c.id) AS chunks,
                            sum(length(c.text)) AS chars
                     FROM library_documents d LEFT JOIN library_chunks c ON c.document_id = d.id
                     WHERE d.active GROUP BY 1, 2 ORDER BY 1, 2"""):
        print(f"  {r['category'] or '-':<22} {r['doc_type']:<22} {r['docs']:4d} docs {r['chunks']:6d} chunks {r['chars'] or 0:>12,d} chars")
    print(db.q_one("SELECT pg_size_pretty(pg_total_relation_size('library_chunks')) AS chunks_size"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["crawl", "download", "ingest", "all", "stats", "add", "baseline"])
    ap.add_argument("url", nargs="?")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="re-chunk documents even if the file is unchanged")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--title")
    ap.add_argument("--category", default="")
    ap.add_argument("--type", dest="dtype", default="other")
    ap.add_argument("--issue-date")
    ap.add_argument("--pdf-url", help="download from here when url points at a page of a shared PDF")
    ap.add_argument("--pages", help="keep only these pages, e.g. 24-38")
    ap.add_argument("--fix-th", action="store_true", help="repair a lost 'Th' ligature (Te -> The)")
    ap.add_argument("--two-columns", action="store_true", help="read pages column by column")
    a = ap.parse_args()
    if a.step in ("crawl", "all"):
        crawl()
    if a.step in ("download", "all"):
        download(a.limit)
    if a.step in ("ingest", "all"):
        ingest(a.dry_run, a.limit, a.force)
    if a.step == "stats":
        stats()
    if a.step == "baseline":
        baseline()
    if a.step == "add":
        if not (a.url and a.title):
            sys.exit("add needs a URL and --title")
        add(a.url, a.title, a.category, a.dtype, a.issue_date, a.pdf_url, a.pages, a.fix_th, a.two_columns)

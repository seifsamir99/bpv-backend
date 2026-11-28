"""Newell Assistant self-update: keeps the library and the knowledge base current.

Every UPDATE_EVERY_DAYS (default 14, env ASSISTANT_UPDATE_DAYS), or when the
office clicks "Check now" on /assistant-knowledge, one run:

  1. Library sync. Re-crawls the tax.gov.ae listings (build_library.crawl_catalog).
     - New documents are downloaded and stored.
     - Known PDFs are HEAD-checked (size / ETag), so only changed files are
       downloaded; files without those headers are re-downloaded and compared
       by hash.
     - Documents gone from a listing are deactivated, but only if the listing
       came back nearly complete.
  2. Knowledge review. Each new or changed document (the review queue:
     library_documents.reviewed_at IS NULL) is read against the knowledge base
     by Claude. It may update or add entries (never Newell Profile). Every
     change must quote the document verbatim, and the quote is checked in code.
  3. Re-verification. Entries last verified over STALE_DAYS ago are checked
     against the library and official sites, and corrected or re-dated.

Every knowledge change is logged in assistant_knowledge_changes with
before/after, reason, source and quote, and can be undone from the page.
Runs are logged in assistant_update_runs.

The scheduler is a daemon thread in the API process (start_scheduler, called by
api.py). It is on when running on Railway or with ASSISTANT_AUTO_UPDATE=1.
Read directives/assistant_library.md.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

import accounting_assistant as persona
import assistant_engine as engine
import assistant_knowledge_service as knowledge
import build_library as builder
import db

log = logging.getLogger(__name__)

UPDATE_EVERY_DAYS = int(os.getenv("ASSISTANT_UPDATE_DAYS", "14"))
SCHEDULER_TICK_SECONDS = 6 * 3600   # how often the thread asks "is a run due?"
RETRY_AFTER_FAILURE_HOURS = 24
STALE_RUN_HOURS = 3                 # a "running" row older than this is a crashed run
STALE_DAYS = 90                     # re-verify knowledge entries older than this
MAX_VERIFY_PER_RUN = 8
MAX_REVIEW_PER_RUN = 15
MAX_DOWNLOADS_PER_RUN = 80
MAX_CHANGES_PER_DOCUMENT = 3
MAX_DOC_CHARS = 150_000             # ~40k tokens of document text per review
PROTECTED_TOPICS = {"Newell Profile"}  # company facts come from Seif, not from the law
REVIEW_TYPES = {"law", "regulation", "cabinet_decision", "ministerial_decision", "fta_decision",
                "directive", "public_clarification", "guide", "other"}
LOCK_KEY = 74_221_001               # pg_advisory_xact_lock key for starting a run

_TYPE_ORDER = {"law": 0, "regulation": 1, "cabinet_decision": 2, "ministerial_decision": 2, "fta_decision": 2,
               "directive": 3, "public_clarification": 3, "guide": 4, "other": 5}


class UpdateError(Exception):
    pass


# ---------------------------------------------------------------------------
# Runs and scheduling
# ---------------------------------------------------------------------------

def _start_run(trigger):
    """Insert a 'running' row unless one is already going. Returns its id or None."""
    def go(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
            cur.execute("UPDATE assistant_update_runs SET status = 'failed', finished_at = now(), "
                        "error = 'Stopped unexpectedly (server restart?)' "
                        "WHERE status = 'running' AND started_at < now() - %s * interval '1 hour'", (STALE_RUN_HOURS,))
            cur.execute("SELECT 1 FROM assistant_update_runs WHERE status = 'running'")
            if cur.fetchone():
                return None
            cur.execute("INSERT INTO assistant_update_runs (trigger) VALUES (%s) RETURNING id", (trigger,))
            return cur.fetchone()[0]
    return db.transaction(go)


def _finish(run_id, status, summary, error=None):
    db.execute("UPDATE assistant_update_runs SET status = %s, finished_at = now(), summary = %s, error = %s "
               "WHERE id = %s", (status, json.dumps(summary, default=str), error, run_id))


def is_due():
    last_done = db.q_one("SELECT max(started_at) AS t FROM assistant_update_runs WHERE status = 'done'")["t"]
    last_any = db.q_one("SELECT max(started_at) AS t FROM assistant_update_runs")["t"]
    now = datetime.now(timezone.utc)
    if last_any and now - last_any < timedelta(hours=RETRY_AFTER_FAILURE_HOURS) and (
            not last_done or last_done < last_any):
        return False  # the last attempt failed recently: wait before retrying
    return not last_done or now - last_done >= timedelta(days=UPDATE_EVERY_DAYS)


def run_in_background(trigger="manual"):
    """Start a run in a thread. Returns the run id, or None if one is already running."""
    run_id = _start_run(trigger)
    if run_id:
        threading.Thread(target=_run, args=(run_id,), daemon=True, name=f"assistant-update-{run_id}").start()
    return run_id


def start_scheduler():
    enabled = os.getenv("ASSISTANT_AUTO_UPDATE", "1" if os.getenv("RAILWAY_ENVIRONMENT") else "0") == "1"
    if not enabled:
        log.info("assistant self-update scheduler is off (set ASSISTANT_AUTO_UPDATE=1 to turn it on)")
        return

    def loop():
        time.sleep(300)  # let the server settle after a deploy
        while True:
            try:
                if is_due():
                    run_id = _start_run("schedule")
                    if run_id:
                        _run(run_id)
            except Exception:
                log.exception("assistant self-update scheduler tick failed")
            time.sleep(SCHEDULER_TICK_SECONDS)

    threading.Thread(target=loop, daemon=True, name="assistant-update-scheduler").start()
    log.info("assistant self-update scheduler on: every %s days", UPDATE_EVERY_DAYS)


def _run(run_id):
    summary = {"new": [], "changed": [], "removed": [], "failed": [], "notes": [],
               "reviewed": 0, "knowledge_changes": 0, "verified": 0, "corrected": 0}
    try:
        sync_library(summary)
        _save_progress(run_id, summary)
        review_queue(run_id, summary)
        _save_progress(run_id, summary)
        verify_stale(run_id, summary)
        _finish(run_id, "done", summary)
        log.info("assistant self-update %s done: %s", run_id, {k: v if isinstance(v, int) else len(v)
                                                               for k, v in summary.items()})
    except Exception as e:
        log.exception("assistant self-update %s failed", run_id)
        _finish(run_id, "failed", summary, f"{type(e).__name__}: {str(e)[:300]}")


def _save_progress(run_id, summary):
    db.execute("UPDATE assistant_update_runs SET summary = %s WHERE id = %s", (json.dumps(summary, default=str), run_id))


# ---------------------------------------------------------------------------
# 1. Library sync
# ---------------------------------------------------------------------------

def _doc_ref(d, doc_id=None):
    return {"id": doc_id, "title": d["title"], "url": d["url"].split("#")[0], "type": d["doc_type"],
            "issued": d.get("issue_date")}


def sync_library(summary):
    catalog, counts = builder.crawl_catalog()
    docs = catalog + builder.extra_docs()
    known = {r["url"]: r for r in db.q(
        "SELECT id, url, listing, doc_type, content_hash, file_size, etag, active FROM library_documents")}
    listed = {d["url"] for d in docs}
    downloads = 0

    for d in docs:
        row = known.get(d["url"])
        if row and row["doc_type"] == "emaratax_manual" and row["active"]:
            continue  # screen manuals: only new ones matter
        if row and row["active"] and row["file_size"]:
            try:
                info = builder.head_info(d)
            except Exception as e:
                summary["failed"].append({"title": d["title"], "error": f"HEAD: {e}"[:200]})
                continue
            if info and info["size"] == row["file_size"] and (not info["etag"] or not row["etag"] or info["etag"] == row["etag"]):
                continue  # unchanged
            time.sleep(builder.DELAY / 2)
        if downloads >= MAX_DOWNLOADS_PER_RUN:
            summary["notes"].append(f"Download limit ({MAX_DOWNLOADS_PER_RUN}) reached; the rest follow next run")
            break
        try:
            data, info = builder.fetch(d)
            downloads += 1
            time.sleep(builder.DELAY)
            d2, digest, pages, chars, chunks = builder.prepare(d, data)
        except Exception as e:
            summary["failed"].append({"title": d["title"], "error": str(e)[:200]})
            continue
        if row and row["content_hash"] == digest:
            db.execute("UPDATE library_documents SET file_size = %s, etag = %s, last_modified = %s, active = true "
                       "WHERE id = %s", (info["size"], info["etag"], info["last_modified"], row["id"]))
            continue  # same content (headers changed, or first time we saw them)
        doc_id = builder.store(d2, digest, pages, chars, chunks, info)
        summary["changed" if row else "new"].append(_doc_ref(d2, doc_id))

    # Gone from a listing (withdrawn, or moved to the archive). Guard against a
    # half-loaded listing taking the library down with it.
    previous = _active_by_listing(known.values())
    for listing, now_count in counts.items():
        before = previous.get(listing, 0)
        if before and now_count < 0.9 * before:
            summary["notes"].append(f"{listing}: listing returned {now_count} items (had {before}); nothing deactivated")
            continue
        for row in known.values():
            if row["listing"] == listing and row["active"] and row["url"] not in listed:
                db.execute("UPDATE library_documents SET active = false, updated_at = now() WHERE id = %s", (row["id"],))
                summary["removed"].append({"id": row["id"], "url": row["url"]})


def _active_by_listing(rows):
    out = {}
    for r in rows:
        if r["active"]:
            out[r["listing"]] = out.get(r["listing"], 0) + 1
    return out


# ---------------------------------------------------------------------------
# 2. Knowledge review of new / changed documents
# ---------------------------------------------------------------------------

SUBMIT_CHANGES_TOOL = {
    "name": "submit_changes",
    "description": "Report what this document changes in the knowledge base. Call exactly once.",
    "input_schema": {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": ["integer", "null"], "description": "Entry to rewrite, or null for a new entry"},
                        "topic": {"type": "string", "description": "For a new entry: its topic (use an existing topic)"},
                        "title": {"type": "string", "description": "For a new entry: its title"},
                        "body": {"type": "string", "description": "The complete new markdown body of the entry"},
                        "reason": {"type": "string", "description": "One sentence: what changed and why it matters to Newell"},
                        "quote": {"type": "string", "description": "A verbatim sentence from the document that justifies the change"},
                    },
                    "required": ["entry_id", "body", "reason", "quote"],
                },
            },
            "summary": {"type": "string", "description": "One line on what the document is about and whether it affects Newell"},
        },
        "required": ["changes", "summary"],
    },
}

REVIEW_RULES = """You maintain the knowledge base of the Newell Assistant, the in-house UAE accounting assistant of Newell Electromechanical Works L.L.C, an MEP subcontractor in Dubai (VAT-registered; subject to Corporate Tax; labour-heavy payroll; works on construction projects for main contractors). The knowledge base is a set of short markdown notes (below), one per topic, which the assistant reads before every answer.

A new or changed official document has been published (in the user message). Decide whether it changes anything in the knowledge base that matters to Newell, then call submit_changes once.

Rules:
- Change an entry only when the document changes a rule, rate, threshold, date, deadline, penalty, procedure or interpretation stated there, or adds one Newell would need. Most documents (e.g. about tobacco, tourists, free zones, banks, investment funds) change nothing: return an empty list.
- When you rewrite an entry, return its complete new body. Keep everything that is still right, keep the style and length, and cite the document (name, number, date) in the text.
- Add a new entry only for something important to Newell that no entry covers. Use an existing topic.
- Never change the "Newell Profile" topic. Company facts come from Seif, not from the law.
- Every change needs a quote: a sentence copied exactly from the document. Changes whose quote isn't in the document are rejected.
- At most {max_changes} changes. Don't invent anything the document doesn't say."""

VERIFY_RULES = """You re-verify one note in the Newell Assistant's knowledge base (Newell Electromechanical Works L.L.C, an MEP subcontractor in Dubai). Check every rule, rate, threshold, date, deadline and penalty it states against the official texts: search_library (the official laws, regulations, decisions and FTA guides) first, and web_search (official UAE sites) for anything newer.

Then call submit_verification once:
- status "current": everything in the note still holds.
- status "outdated": something is wrong or has changed. Give the complete corrected body (keep everything still right, same style), the reason, the source URL and a quote from that source.
Don't mark it outdated without a source. Don't rewrite for style."""

SUBMIT_VERIFICATION_TOOL = {
    "name": "submit_verification",
    "description": "Report the result of re-verifying the note. Call exactly once, at the end.",
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["current", "outdated"]},
            "body": {"type": "string", "description": "For outdated: the complete corrected body"},
            "reason": {"type": "string"},
            "source_url": {"type": "string"},
            "source_title": {"type": "string"},
            "quote": {"type": "string", "description": "A sentence from the source that settles it"},
        },
        "required": ["status", "reason"],
    },
}


def _client():
    from anthropic import Anthropic
    return Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), max_retries=3)


def _entries():
    return knowledge.list_entries()["entries"]


def _entries_text(entries):
    parts = []
    for e in entries:
        if not e["active"]:
            continue
        parts.append(f'<entry id="{e["id"]}" topic="{e["topic"]}" title="{e["title"]}" '
                     f'last_verified="{e["lastVerified"] or ""}">\n{e["body"]}\n</entry>')
    return "<knowledge_base>\n" + "\n\n".join(parts) + "\n</knowledge_base>"


def _norm(text):
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _quote_found(quote, text):
    q = _norm(quote).strip(" .\"'“”")
    return len(q) >= 20 and q in _norm(text)


def _document_text(doc_id):
    rows = db.q("SELECT heading, text FROM library_chunks WHERE document_id = %s ORDER BY seq", (doc_id,))
    return "\n\n".join(f"## {r['heading']}\n{r['text']}" for r in rows)


def _ask_for_tool(client, system, messages, tools, tool_name, max_rounds=6):
    """Run a small tool loop until Claude calls tool_name. Returns (input, sources seen)."""
    convo, cited, searched, calcs, nudged = list(messages), [], [], [], False
    library_urls, library_text = set(), []
    for _ in range(max_rounds):
        msg = client.messages.create(model=engine.MODEL, max_tokens=16000, system=system, messages=convo,
                                     tools=tools, thinking={"type": "adaptive"})
        u = msg.usage
        log.info("assistant update %s usage: in=%s cache_read=%s out=%s stop=%s", tool_name, u.input_tokens,
                 getattr(u, "cache_read_input_tokens", None), u.output_tokens, msg.stop_reason)
        engine._collect(msg.content, cited, searched)
        call = next((b for b in msg.content if b.type == "tool_use" and b.name == tool_name), None)
        if call is not None:
            web = {s["url"].split("#")[0] for s in cited + searched} - library_urls
            return (call.input if isinstance(call.input, dict) else {}), {
                "library_urls": library_urls, "library_text": library_text, "web_urls": web}
        if msg.stop_reason == "pause_turn":
            convo = convo + [{"role": "assistant", "content": msg.content}]
        elif msg.stop_reason == "tool_use":
            results = engine._run_client_tools(msg.content, calcs)
            for r in results:
                for block in r.get("content") if isinstance(r.get("content"), list) else []:
                    if block.get("type") == "search_result":
                        library_urls.add(block["source"].split("#")[0])
                        library_text.append(block["content"][0]["text"])
            convo = convo + [{"role": "assistant", "content": msg.content}, {"role": "user", "content": results}]
        elif not nudged:
            nudged = True
            convo = convo + [{"role": "assistant", "content": msg.content},
                             {"role": "user", "content": f"Call {tool_name} now."}]
        else:
            break
    return None, {"library_urls": set(), "library_text": [], "web_urls": set()}


def _log_change(run_id, entry_id, action, before, after, reason, source_title, source_url, quote):
    db.execute(
        "INSERT INTO assistant_knowledge_changes (run_id, entry_id, action, before, after, reason, "
        "source_title, source_url, quote) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (run_id, entry_id, action, json.dumps(before) if before else None, json.dumps(after) if after else None,
         reason or "", source_title or "", source_url or "", quote or ""))


def _snapshot(entry):
    return {k: entry.get(k) for k in ("topic", "title", "body", "sources", "lastVerified")} if entry else None


def _with_source(sources, cite):
    sources = (sources or "").strip()
    return sources if cite in sources else (f"{sources}; {cite}" if sources else cite)


def review_queue(run_id, summary):
    queue = db.q("SELECT id, title, url, doc_type, category, issue_date FROM library_documents "
                 "WHERE active AND reviewed_at IS NULL")
    queue = [d for d in queue if d["doc_type"] in REVIEW_TYPES]
    queue.sort(key=lambda d: (_TYPE_ORDER.get(d["doc_type"], 9), -(d["issue_date"].toordinal() if d["issue_date"] else 0)))
    if len(queue) > MAX_REVIEW_PER_RUN:
        summary["notes"].append(f"{len(queue) - MAX_REVIEW_PER_RUN} documents left in the review queue for next run")
    if not queue:
        return
    client = _client()
    for doc in queue[:MAX_REVIEW_PER_RUN]:
        entries = _entries()
        by_id = {e["id"]: e for e in entries}
        system = [
            {"type": "text", "text": REVIEW_RULES.format(max_changes=MAX_CHANGES_PER_DOCUMENT)},
            {"type": "text", "text": _entries_text(entries), "cache_control": {"type": "ephemeral"}},
        ]
        text = _document_text(doc["id"])
        if len(text) > MAX_DOC_CHARS:
            text = text[:MAX_DOC_CHARS] + "\n\n[The rest of the document is not shown.]"
        issued = doc["issue_date"].isoformat() if doc["issue_date"] else "unknown"
        user = (f'<document title="{doc["title"]}" type="{doc["doc_type"]}" category="{doc["category"]}" '
                f'issued="{issued}" url="{doc["url"]}">\n{text}\n</document>\n\nReview it and call submit_changes.')
        result, _ = _ask_for_tool(client, system, [{"role": "user", "content": user}],
                                  [SUBMIT_CHANGES_TOOL], "submit_changes", max_rounds=3)
        if result is None:
            summary["failed"].append({"title": doc["title"], "error": "review gave no result"})
            continue  # stays in the queue for next run
        cite = f"{doc['title']} ({issued})"
        for ch in (result.get("changes") or [])[:MAX_CHANGES_PER_DOCUMENT]:
            applied = _apply_review_change(run_id, ch, by_id, text, doc, cite)
            if applied:
                summary["knowledge_changes"] += 1
            elif applied is False:
                summary["notes"].append(f"Rejected a change from {doc['title'][:80]}: quote not found in the document")
        db.execute("UPDATE library_documents SET reviewed_at = now() WHERE id = %s", (doc["id"],))
        summary["reviewed"] += 1


def _apply_review_change(run_id, ch, by_id, doc_text, doc, cite):
    """True = applied, False = rejected (bad quote), None = skipped (nothing to do / not allowed)."""
    body = str(ch.get("body") or "").strip()
    if not body:
        return None
    if not _quote_found(ch.get("quote"), doc_text):
        return False
    today = date.today().isoformat()
    entry_id = ch.get("entry_id")
    try:
        if entry_id:
            entry = by_id.get(int(entry_id))
            if not entry or entry["topic"] in PROTECTED_TOPICS or entry["body"].strip() == body:
                return None
            after = knowledge.update_entry(entry["id"], {"body": body, "lastVerified": today,
                                                         "sources": _with_source(entry["sources"], cite)}, by="assistant")
            _log_change(run_id, entry["id"], "update", _snapshot(entry), _snapshot(after), ch.get("reason"),
                        doc["title"], doc["url"], ch.get("quote"))
        else:
            topic = str(ch.get("topic") or "").strip()
            title = str(ch.get("title") or "").strip()
            if not topic or not title or topic in PROTECTED_TOPICS:
                return None
            after = knowledge.create_entry({"topic": topic, "title": title, "body": body, "sources": cite,
                                            "lastVerified": today}, by="assistant")
            _log_change(run_id, after["id"], "create", None, _snapshot(after), ch.get("reason"),
                        doc["title"], doc["url"], ch.get("quote"))
    except knowledge.KnowledgeError as e:
        log.warning("self-update change skipped: %s", e)
        return None
    return True


# ---------------------------------------------------------------------------
# 3. Re-verify stale entries
# ---------------------------------------------------------------------------

def verify_stale(run_id, summary, limit=MAX_VERIFY_PER_RUN):
    cutoff = date.today() - timedelta(days=STALE_DAYS)
    stale = [e for e in _entries() if e["active"] and e["topic"] not in PROTECTED_TOPICS
             and (not e["lastVerified"] or date.fromisoformat(e["lastVerified"]) < cutoff)]
    stale.sort(key=lambda e: e["lastVerified"] or "")
    if not stale:
        return
    client = _client()
    system = [{"type": "text", "text": VERIFY_RULES},
              {"type": "text", "text": persona.knowledge_text(), "cache_control": {"type": "ephemeral"}}]
    tools = [engine.SEARCH_LIBRARY_TOOL, engine.CHECK_WEB_SEARCH, SUBMIT_VERIFICATION_TOOL]
    today = date.today().isoformat()
    for entry in stale[:limit]:
        user = (f'Re-verify this note (last verified {entry["lastVerified"] or "never"}):\n'
                f'<entry topic="{entry["topic"]}" title="{entry["title"]}">\n{entry["body"]}\n</entry>')
        result, seen = _ask_for_tool(client, system, [{"role": "user", "content": user}], tools, "submit_verification")
        if not result:
            summary["failed"].append({"title": entry["title"], "error": "re-verification gave no result"})
            continue
        status = result.get("status")
        source = str(result.get("source_url") or "").split("#")[0]
        body = str(result.get("body") or "").strip()
        # A correction needs a source the checker actually saw. From the library
        # the quote is checked word for word; a web result's text is encrypted,
        # so there the source URL must at least be one the search returned.
        if source in seen["library_urls"]:
            backed = any(_quote_found(result.get("quote"), t) for t in seen["library_text"])
        else:
            backed = source in seen["web_urls"]
        if status == "outdated" and body and backed and body != entry["body"].strip():
            after = knowledge.update_entry(entry["id"], {"body": body, "lastVerified": today,
                                                         "sources": _with_source(entry["sources"], result.get("source_title") or source)},
                                           by="assistant")
            _log_change(run_id, entry["id"], "update", _snapshot(entry), _snapshot(after), result.get("reason"),
                        result.get("source_title"), source, result.get("quote"))
            summary["corrected"] += 1
            summary["knowledge_changes"] += 1
        elif status == "current":
            after = knowledge.update_entry(entry["id"], {"lastVerified": today}, by="assistant")
            _log_change(run_id, entry["id"], "verify", _snapshot(entry), _snapshot(after), result.get("reason"),
                        result.get("source_title"), source, result.get("quote"))
            summary["verified"] += 1
        else:
            summary["notes"].append(f"Could not settle \"{entry['title']}\" (no usable source); left as it was")


# ---------------------------------------------------------------------------
# Reading the log, undo
# ---------------------------------------------------------------------------

def _change_json(r):
    return {
        "id": r["id"], "runId": r["run_id"], "entryId": r["entry_id"], "action": r["action"],
        "before": r["before"], "after": r["after"], "reason": r["reason"], "sourceTitle": r["source_title"],
        "sourceUrl": r["source_url"], "quote": r["quote"],
        "createdAt": r["created_at"].isoformat(), "undoneAt": r["undone_at"].isoformat() if r["undone_at"] else None,
    }


def status():
    runs = db.q("SELECT id, trigger, status, started_at, finished_at, summary, error FROM assistant_update_runs "
                "ORDER BY started_at DESC LIMIT 10")
    changes = db.q("SELECT * FROM assistant_knowledge_changes ORDER BY created_at DESC LIMIT 60")
    last_done = next((r for r in runs if r["status"] == "done"), None)
    next_due = (last_done["started_at"] + timedelta(days=UPDATE_EVERY_DAYS)).isoformat() if last_done else None
    queue = db.q_one("SELECT count(*) AS n FROM library_documents WHERE active AND reviewed_at IS NULL "
                     "AND doc_type <> 'emaratax_manual'")["n"]
    lib = db.q_one("SELECT count(*) AS n, max(fetched_at) AS t FROM library_documents WHERE active")
    return {
        "everyDays": UPDATE_EVERY_DAYS,
        "nextDue": next_due,
        "running": any(r["status"] == "running" for r in runs),
        "reviewQueue": queue,
        "library": {"documents": lib["n"], "lastFetched": lib["t"].isoformat() if lib["t"] else None},
        "runs": [{"id": r["id"], "trigger": r["trigger"], "status": r["status"],
                  "startedAt": r["started_at"].isoformat(),
                  "finishedAt": r["finished_at"].isoformat() if r["finished_at"] else None,
                  "summary": r["summary"], "error": r["error"]} for r in runs],
        "changes": [_change_json(c) for c in changes],
    }


def undo(change_id):
    """Put an entry back the way it was before one assistant change."""
    ch = db.q_one("SELECT * FROM assistant_knowledge_changes WHERE id = %s", (change_id,))
    if not ch:
        raise UpdateError("Change not found")
    if ch["undone_at"]:
        raise UpdateError("Already undone")
    entry = knowledge.get_entry(ch["entry_id"]) if ch["entry_id"] else None
    after, before = ch["after"] or {}, ch["before"] or {}
    if ch["action"] == "create":
        if entry:
            if entry["body"].strip() != (after.get("body") or "").strip():
                raise UpdateError("This entry was edited after the assistant added it. Delete it on the page instead.")
            knowledge.delete_entry(entry["id"])
    else:
        if not entry:
            raise UpdateError("The entry no longer exists")
        if ch["action"] == "update" and entry["body"].strip() != (after.get("body") or "").strip():
            raise UpdateError("This entry was edited after the assistant changed it, so it can't be undone automatically.")
        restore = {"lastVerified": before.get("lastVerified")}
        if ch["action"] == "update":
            restore.update({k: before.get(k) for k in ("topic", "title", "body", "sources")})
        knowledge.update_entry(entry["id"], restore, by="office")
    db.execute("UPDATE assistant_knowledge_changes SET undone_at = now() WHERE id = %s", (change_id,))

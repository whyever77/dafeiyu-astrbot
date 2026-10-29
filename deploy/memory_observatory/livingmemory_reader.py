"""Read-only projection for the inspected LivingMemory 2.7.0-beta.2 schema.

This file also runs through SSH stdin with Python's standard library. It imports
no plugin code, opens no conversation database, and has no network/write calls.
No source message transcripts or arbitrary metadata leave the database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SUPPORTED_VERSION = "2.7.0-beta.2"
MAX_RECORDS = 500
ATOM_KINDS = {"episodic": "episode", "factual": "knowledge", "preference": "preference",
              "planned": "plan", "relational": "relation", "unknown": "unclassified"}
DEFAULT_DB = "/opt/dafeiyu/astrbot/data/plugin_data/astrbot_plugin_livingmemory/livingmemory.db"
DEFAULT_METADATA = "/opt/dafeiyu/astrbot/data/plugins/astrbot_plugin_livingmemory/metadata.yaml"


class SourceError(ValueError):
    """Only fixed, non-sensitive messages may cross the process boundary."""


def object_json(value):
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def clean(value, limit):
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if ord(c) >= 32 or c in "\n\t").strip()[:limit]


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def probability(value, default=None):
    return float(value) if finite(value) and 0 <= value <= 1 else default


def epoch(value):
    if not finite(value) or value < 0:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def scope_key(session, persona):
    # A null persona is an explicit upstream value, distinct from any named
    # persona. A missing key is quarantined by the caller. Never infer owner.
    if not isinstance(session, str) or not session.strip():
        return None
    if persona is not None and not isinstance(persona, str):
        return None
    digest = hashlib.sha256(json.dumps([session, persona], ensure_ascii=False).encode()).hexdigest()[:16]
    return "LM / " + digest


def version_from_file(path):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    raise SourceError("LivingMemory version missing")


def read_snapshot(db_path, version, *, now=None):
    if version != SUPPORTED_VERSION:
        raise SourceError("LivingMemory version not supported; inspect schema before updating adapter")
    at = now or datetime.now(timezone.utc)
    if at.tzinfo is None:
        raise SourceError("Snapshot clock must include timezone")
    at = at.astimezone(timezone.utc)
    counts = {"documents_total": 0, "atoms_total": 0, "documents_loaded": 0, "atoms_loaded": 0,
              "inactive_documents": 0, "inactive_atoms": 0, "unscoped": 0, "invalid_records": 0,
              "truncated_records": 0, "capacity_omitted": 0, "orphan_or_scope_mismatch_atoms": 0}
    records, edges, parents, scopes = [], [], {}, {}
    path = Path(db_path).resolve(strict=True)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION}
        db.set_authorizer(lambda action, _a, _b, _c, _d: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        db.execute("BEGIN")
        # Explicit columns fail closed on schema drift. No CREATE/migration code.
        documents = db.execute("SELECT id, text, metadata FROM documents ORDER BY id DESC").fetchall()
        atoms = db.execute("SELECT id, parent_memory_id, atom_type, content, importance, confidence, "
                           "created_at, last_accessed_at, last_reinforced_at, expires_at, status, "
                           "reinforcement_count, session_id, persona_id, decay_type, ttl_days "
                           "FROM memory_atoms ORDER BY id DESC").fetchall()
        counts["documents_total"], counts["atoms_total"] = len(documents), len(atoms)
        graph_count = db.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]

        def append(row, meta, ident, kind, created_value, accessed_value, text, confidence=None, extra=None):
            if "session_id" not in meta or "persona_id" not in meta:
                counts["unscoped"] += 1
                return None
            scope = scope_key(meta["session_id"], meta["persona_id"])
            if not scope:
                counts["unscoped"] += 1
                return None
            created = epoch(created_value)
            if not created or created > at or not clean(text, 4000):
                counts["invalid_records"] += 1
                return None
            if len(records) >= MAX_RECORDS:
                counts["capacity_omitted"] += 1
                return None
            accessed = epoch(accessed_value)
            # Preserve uncertainty explicitly instead of inventing a recall.
            accessed_valid = accessed is not None and created <= accessed <= at
            accessed = accessed if accessed_valid else created
            summary = clean(meta.get("persona_summary") or meta.get("canonical_summary") or text, 500)
            topics = [clean(t, 40) for t in meta.get("topics", []) if isinstance(t, str) and clean(t, 40)] if isinstance(meta.get("topics"), list) else []
            topics = list(dict.fromkeys(topics))[:8]
            title = clean(text.splitlines()[0], 64) or ident
            shortened = len(text) > 4000
            counts["truncated_records"] += int(shortened)
            upstream = {"record_id": ident, "record_type": "document" if kind == "document" else "atom",
                        "status": "active", "version": version, "access_time_known": accessed_valid,
                        "truncated": shortened, "relations": [], **(extra or {})}
            count = meta.get("access_count")
            if finite(count) and count >= 0:
                upstream["access_count"] = int(count)
            record = {"id": ident, "scope": scope, "title": title, "text": clean(text, 4000), "gist": summary,
                      "kind": kind, "topics": topics, "created_at": created.isoformat(),
                      "last_recalled_at": accessed.isoformat(), "importance": probability(meta.get("importance"), .5),
                      "confidence": probability(confidence), "pinned": False,
                      "source": "LivingMemory " + version + " / " + ident, "upstream": upstream}
            records.append(record)
            scopes[scope] = {"label": "会话 " + scope[-6:] + (" · 未指定人格" if meta["persona_id"] is None else " · 独立人格"),
                             "records": scopes.get(scope, {}).get("records", 0) + 1}
            return record

        for row in documents:
            meta = object_json(row["metadata"])
            if meta.get("status", "active") != "active":
                counts["inactive_documents"] += 1
                continue
            record = append(row, meta, "lm-doc-" + str(row["id"]), "document", meta.get("create_time"),
                            meta.get("last_access_time"), row["text"] or "")
            if record:
                parents[row["id"]] = (record, meta)
                counts["documents_loaded"] += 1
        for row in atoms:
            expires = row["expires_at"]
            if row["status"] != "active" or (finite(expires) and expires > 0 and expires <= at.timestamp()):
                counts["inactive_atoms"] += 1
                continue
            parent = parents.get(row["parent_memory_id"])
            scope = scope_key(row["session_id"], row["persona_id"])
            if not parent or not scope or scope != parent[0]["scope"]:
                counts["orphan_or_scope_mismatch_atoms"] += 1
                continue
            meta = {"session_id": row["session_id"], "persona_id": row["persona_id"], "importance": row["importance"]}
            extra = {"atom_type": clean(row["atom_type"], 40), "parent_id": parent[0]["id"],
                     "reinforcement_count": int(row["reinforcement_count"] or 0),
                     "decay_type": clean(row["decay_type"], 40)}
            if epoch(expires) and expires > 0:
                extra["expires_at"] = epoch(expires).isoformat()
            if finite(row["ttl_days"]):
                extra["ttl_days"] = row["ttl_days"]
            record = append(row, meta, "lm-atom-" + str(row["id"]), ATOM_KINDS.get(row["atom_type"], "unclassified"),
                            row["created_at"], row["last_accessed_at"], row["content"] or "", row["confidence"], extra)
            if record:
                counts["atoms_loaded"] += 1
                edges.append({"source": parent[0]["id"], "target": record["id"],
                              "label": "原库来源：文档 → 记忆原子", "weight": 1, "inferred": False})
        # Keep original entity relations as evidence on their source document.
        # Never misrepresent entity-to-entity edges as document-to-document ones.
        for parent_id, (record, _) in parents.items():
            relations = db.execute("SELECT e.id, e.relation_type, s.node_value AS source, t.node_value AS target "
                                   "FROM graph_edges e JOIN graph_nodes s ON s.id=e.source_node_id "
                                   "JOIN graph_nodes t ON t.id=e.target_node_id "
                                   "WHERE e.source_memory_id=? AND e.status='active' ORDER BY e.id LIMIT 5", (parent_id,)).fetchall()
            record["upstream"]["relations"] = [{"id": str(r["id"]), "source": clean(r["source"], 80),
                                               "relation": clean(r["relation_type"], 60), "target": clean(r["target"], 80)} for r in relations]
    finally:
        db.close()
    return {"schema": "memory-observatory/v1", "as_of": at.isoformat(), "label": "LivingMemory 只读观测",
            "memories": records, "edges": edges,
            "source_info": {"version": version, "synced_at": at.isoformat(), "counts": counts,
                            "graph_edges_total": graph_count, "scopes": scopes}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--metadata", default=DEFAULT_METADATA)
    args = parser.parse_args()
    try:
        result = read_snapshot(args.db, version_from_file(args.metadata))
        print(json.dumps({"ok": True, "snapshot": result}, ensure_ascii=False, allow_nan=False))
    except Exception:
        # stderr/traceback must not expose paths, SQL data or credentials.
        print(json.dumps({"ok": False, "error": "LivingMemory read-only source unavailable or schema changed"}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()

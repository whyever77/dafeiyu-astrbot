"""Pure memory projection and cue retrieval. No I/O, model calls or writeback.

The half-lives are illustrative policy parameters, not fitted human-memory data.
Retrievability and confidence deliberately remain separate. Viewing a projection
cannot change the source records; rehearsals are explicit, ephemeral experiments.
"""

from __future__ import annotations

import copy
import math
import re
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
KINDS = {"episode": 30, "preference": 120, "knowledge": 90, "method": 180,
         "document": 90, "plan": 30, "relation": 120, "unclassified": 30}
MAX_MEMORIES = 500


class InvalidSnapshot(ValueError):
    pass


def timestamp(value):
    if not isinstance(value, str):
        raise InvalidSnapshot("时间必须是带时区的 ISO 8601 字符串")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidSnapshot("时间格式无效") from exc
    if parsed.tzinfo is None:
        raise InvalidSnapshot("时间必须包含时区")
    return parsed.astimezone(UTC)


def string(value, name, limit, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise InvalidSnapshot(f"{name}必须是长度不超过 {limit} 的文本")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise InvalidSnapshot(f"{name}包含控制字符")
    return value.strip()


def number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InvalidSnapshot(f"{name}必须是有限数字")
    if not low <= value <= high:
        raise InvalidSnapshot(f"{name}必须在 {low} 与 {high} 之间")
    return float(value)


def upstream_metadata(value):
    """Keep an explicit evidence allowlist; never relay arbitrary metadata."""
    if not isinstance(value, dict):
        raise InvalidSnapshot("upstream 必须是对象")
    result = {}
    for key in ("record_id", "record_type", "status", "version", "atom_type", "parent_id", "decay_type"):
        if key in value:
            result[key] = string(value[key], key, 100, empty=True)
    for key in ("access_time_known", "truncated"):
        if key in value:
            if not isinstance(value[key], bool):
                raise InvalidSnapshot(f"{key} 必须是布尔值")
            result[key] = value[key]
    for key in ("access_count", "reinforcement_count"):
        if key in value:
            result[key] = int(number(value[key], key, 0, 1_000_000_000))
    if "ttl_days" in value:
        result["ttl_days"] = number(value["ttl_days"], "ttl_days", -1_000_000, 1_000_000)
    if "expires_at" in value:
        result["expires_at"] = timestamp(value["expires_at"]).isoformat()
    relations = value.get("relations", [])
    if not isinstance(relations, list) or len(relations) > 5:
        raise InvalidSnapshot("每条记录最多展示 5 条原图谱关系")
    result["relations"] = []
    for relation in relations:
        if not isinstance(relation, dict):
            raise InvalidSnapshot("图谱关系必须是对象")
        result["relations"].append({k: string(relation.get(k), k, 100, empty=True)
                                    for k in ("id", "source", "relation", "target")})
    return result


def normalize(payload, *, allow_empty=False):
    """Validate the explicit v1 exchange format, dropping all unknown metadata.

    The versioned LivingMemory reader maps its schema here. Arbitrary native
    exports are still not guessed. Empty snapshots are for trusted readers only.
    """
    if not isinstance(payload, dict) or payload.get("schema") != "memory-observatory/v1":
        raise InvalidSnapshot("需要 memory-observatory/v1 格式；请先按接入文档转换导出文件")
    raw = payload.get("memories")
    if not isinstance(raw, list) or not (0 if allow_empty else 1) <= len(raw) <= MAX_MEMORIES:
        raise InvalidSnapshot(f"memories 必须包含 1–{MAX_MEMORIES} 条记忆")
    as_of = timestamp(payload.get("as_of"))
    memories, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise InvalidSnapshot("记忆必须是对象")
        ident = string(item.get("id"), "id", 100)
        if ident in seen:
            raise InvalidSnapshot("记忆 id 重复")
        seen.add(ident)
        kind = item.get("kind", "episode")
        if not isinstance(kind, str) or kind not in KINDS:
            raise InvalidSnapshot("kind 不是支持的记忆类型")
        created = timestamp(item.get("created_at"))
        recalled = timestamp(item.get("last_recalled_at", item.get("created_at")))
        if created > as_of or recalled > as_of or recalled < created:
            raise InvalidSnapshot("创建/召回时间不能晚于快照，召回不能早于创建")
        topics = item.get("topics", [])
        if not isinstance(topics, list) or len(topics) > 8:
            raise InvalidSnapshot("每条记忆最多 8 个主题")
        topics = list(dict.fromkeys(string(t, "主题", 40) for t in topics))
        pinned = item.get("pinned", False)
        if not isinstance(pinned, bool):
            raise InvalidSnapshot("pinned 必须是布尔值")
        confidence = item.get("confidence")
        if confidence is not None:
            confidence = number(confidence, "confidence", 0, 1)
        assumed = item.get("stability_assumed", "half_life_days" not in item)
        if not isinstance(assumed, bool):
            raise InvalidSnapshot("stability_assumed 必须是布尔值")
        memories.append({
            "id": ident,
            "scope": string(item.get("scope"), "scope", 120),
            "title": string(item.get("title"), "title", 120),
            "text": string(item.get("text"), "text", 4000),
            "gist": string(item.get("gist", item.get("title")), "gist", 500),
            "kind": kind, "topics": topics,
            "created_at": created.isoformat(), "last_recalled_at": recalled.isoformat(),
            "importance": number(item.get("importance", 0.5), "importance", 0, 1),
            "confidence": confidence, "pinned": pinned,
            "half_life_days": number(item.get("half_life_days", KINDS[kind]), "half_life_days", 1, 3650),
            "stability_assumed": assumed,
            "source": string(item.get("source", "导入快照 · 未提供原始出处"), "source", 300),
        })
        if "upstream" in item:
            memories[-1]["upstream"] = upstream_metadata(item["upstream"])
    by_id = {m["id"]: m for m in memories}
    edges, pairs = [], set()
    explicit = payload.get("edges", [])
    if not isinstance(explicit, list) or len(explicit) > 4000:
        raise InvalidSnapshot("edges 必须是列表且不超过 4000 条")
    for edge in explicit:
        if not isinstance(edge, dict):
            raise InvalidSnapshot("关联必须是对象")
        a, b = edge.get("source"), edge.get("target")
        if not isinstance(a, str) or not isinstance(b, str) or a not in by_id or b not in by_id or a == b:
            raise InvalidSnapshot("关联必须指向两条不同的有效记忆")
        if by_id[a]["scope"] != by_id[b]["scope"]:
            raise InvalidSnapshot("禁止跨作用域关联")
        pair = tuple(sorted((a, b)))
        if pair in pairs:
            continue
        pairs.add(pair)
        inferred = edge.get("inferred", False)
        if not isinstance(inferred, bool):
            raise InvalidSnapshot("inferred 必须是布尔值")
        edges.append({"source": a, "target": b,
                      "label": string(edge.get("label", "来源标注的关联"), "关联说明", 80),
                      "inferred": inferred, "weight": number(edge.get("weight", .7), "weight", 0, 1)})
    # Shared themes are structural cues, never evidence of a real-world fact.
    # Bound each node's automatic neighbours to keep large imports readable.
    for index, a in enumerate(memories):
        count = sum(e["inferred"] and e["source"] == a["id"] for e in edges)
        for b in memories[index + 1:]:
            shared = sorted(set(a["topics"]) & set(b["topics"]))
            pair = tuple(sorted((a["id"], b["id"])))
            if a["scope"] == b["scope"] and shared and pair not in pairs and count < 4:
                edges.append({"source": a["id"], "target": b["id"], "label": "共同主题：" + "、".join(shared),
                              "inferred": True, "weight": min(.65, .25 + .15 * len(shared))})
                pairs.add(pair)
                count += 1
    return {"schema": "memory-observatory/v1", "as_of": as_of.isoformat(),
            "label": string(payload.get("label", "导入记忆"), "label", 80),
            "memories": memories, "edges": edges}


def retention(memory, at, rehearsed=False, pinned=False):
    age = max(0, (at - timestamp(memory["last_recalled_at"])).total_seconds() / 86400)
    # A stability sensitivity experiment, not a fabricated real recall event.
    stability = memory["half_life_days"] * (1.6 if rehearsed else 1)
    permanent = memory["pinned"] or pinned
    detail = 1.0 if permanent else 2 ** (-age / stability)
    gist = 1.0 if permanent else 2 ** (-age / (stability * 3))
    return {"detail": round(detail, 6), "gist_retention": round(gist, 6), "age_days": round(age, 1),
            "stability_days": round(stability, 1), "protected": permanent}


def cue_score(query, memory):
    text = (memory["title"] + " " + memory["text"] + " " + " ".join(memory["topics"])).casefold()
    words = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", query.casefold())
    tokens = set()
    for word in words:
        if re.fullmatch(r"[\u3400-\u9fff]+", word) and len(word) > 2:
            tokens.update(word[i:i + 2] for i in range(len(word) - 1))
        else:
            tokens.add(word)
    return sum(t in text for t in tokens) / max(1, len(tokens))


def project(snapshot, *, scope, days=0, query="", rehearsed=(), pinned=()):
    days = number(days, "模拟天数", 0, 730)
    query = string(query, "线索", 200, empty=True)
    scopes = sorted({m["scope"] for m in snapshot["memories"]})
    if scope not in scopes and not (not scopes and scope is None):
        raise InvalidSnapshot("请选择有效作用域")
    # Filter before lexical retrieval AND graph spreading.
    records = [m for m in snapshot["memories"] if m["scope"] == scope]
    ids = {m["id"] for m in records}
    for values in (rehearsed, pinned):
        if not isinstance(values, (list, tuple)) or len(values) > MAX_MEMORIES or any(not isinstance(i, str) or i not in ids for i in values):
            raise InvalidSnapshot("实验对象必须属于当前作用域")
    at = timestamp(snapshot["as_of"]) + timedelta(days=days)
    memories = copy.deepcopy(records)
    edges = [copy.deepcopy(e) for e in snapshot["edges"] if e["source"] in ids and e["target"] in ids]
    for m in memories:
        m.update(retention(m, at, m["id"] in rehearsed, m["id"] in pinned))
        m["simulated_rehearsal"] = m["id"] in rehearsed
        m["simulated_pin"] = m["id"] in pinned
        m["curve"] = [{"day": d, **retention(m, timestamp(snapshot["as_of"]) + timedelta(days=d),
                                             m["id"] in rehearsed, m["id"] in pinned)} for d in range(0, 731, 10)]
    trace = []
    if query:
        scored = [(cue_score(query, m), m) for m in memories]
        direct = sorted([(score * (.35 + .45 * m["gist_retention"] + .2 * m["importance"]), m)
                         for score, m in scored if score > 0], key=lambda x: (-x[0], x[1]["id"]))[:5]
        seeds = {m["id"] for _, m in direct}
        for score, m in direct:
            trace.append({"id": m["id"], "score": round(score, 3), "via": None, "reason": "文本/主题线索匹配"})
        neighbours = {}
        for edge in edges:
            for a, b in ((edge["source"], edge["target"]), (edge["target"], edge["source"])):
                if a in seeds and b not in seeds:
                    score = edge["weight"] * .35
                    if b not in neighbours or neighbours[b]["score"] < score:
                        neighbours[b] = {"id": b, "score": round(score, 3), "via": a, "reason": edge["label"]}
        trace.extend(sorted(neighbours.values(), key=lambda n: (-n["score"], n["id"]))[:3])
    return {"schema": snapshot["schema"], "label": snapshot["label"], "as_of": snapshot["as_of"],
            "projected_at": at.isoformat(), "scope": scope, "scopes": scopes, "days": days,
            "memories": memories, "edges": edges, "trace": trace,
            "stats": {"total": len(memories), "connections": len(edges),
                      "clear": sum(m["detail"] >= .5 for m in memories),
                      "fading": sum(.1 <= m["detail"] < .5 for m in memories),
                      "dormant": sum(m["detail"] < .1 for m in memories),
                      "protected": sum(m["protected"] for m in memories)}}

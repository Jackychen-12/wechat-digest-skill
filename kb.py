#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════════════════
 墨摘 · 知识库存储层 (wechat-digest skill)
 ─────────────────────────────────────────────────────────────────────────
 一个「持续累积」的本地知识库：把每次采集到的 articles_*.json 去重合并进
 单文件 output/knowledge_base.json，并承载本地 agent 写回的结构性分析
 （五段式逐篇精析 + 主题聚类 + 标签倒排 + 跨文章交叉引用 + 收藏夹）。

 纯标准库实现，无任何第三方依赖——无网 / 无 pip 也能跑。

 设计哲学（沿用 redbook skill）：采集用脚本，分析用本地 agent（默认不调外部
 大模型 API）。本模块只负责「存 / 取 / 合并 / 索引 / 导出」，分析内容由 agent
 通过 `apply` / `set-analysis` / `set-meta` 写回。

 常用命令：
   python3 kb.py ingest output/articles_20250624.json   # 采集结果入库（去重合并）
   python3 kb.py stats                                   # 看进度（篇数/已分析比例/主题分布）
   python3 kb.py list --unanalyzed --json               # 取「待分析」批次给 agent
   python3 kb.py apply --file batch.json                 # agent 把一批拆解结果写回
   python3 kb.py export-html                             # 生成离线 HTML 工作台
════════════════════════════════════════════════════════════════════════════
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
KB_PATH = os.path.join(OUTPUT_DIR, "knowledge_base.json")

KB_VERSION = 1

# 五段式分析的固定字段（与 SKILL.md / 离线页保持一致）
ANALYSIS_FIELDS = ("summary", "viewpoints", "data", "tags", "audience")


# ════════════════════════════════════════════════════════════════
#  规范化 id —— 与 backend/main.ts 的 canonicalKey 等价
#  优先用链接里的 sn= ；否则取 /s/ 之后片段；再否则 sha1(link or title)
# ════════════════════════════════════════════════════════════════

def canonical_id(link="", title=""):
    link = (link or "").strip()
    if link:
        m = re.search(r"[?&]sn=([0-9a-fA-F]+)", link)
        if m:
            return m.group(1).lower()
        m = re.search(r"mp\.weixin\.qq\.com/s[/?]([^#&]*)", link)
        if m and m.group(1):
            frag = m.group(1)
            # 短而干净的片段直接用，否则散列，避免 key 过长
            if re.fullmatch(r"[0-9A-Za-z_-]{6,64}", frag):
                return frag.lower()
        return "h" + hashlib.sha1(link.encode("utf-8")).hexdigest()[:16]
    if title:
        return "t" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]
    return "x" + hashlib.sha1(str(datetime.now()).encode("utf-8")).hexdigest()[:16]


# ════════════════════════════════════════════════════════════════
#  读写（原子保存）
# ════════════════════════════════════════════════════════════════

def empty_kb():
    return {
        "version": KB_VERSION,
        "updatedAt": None,
        "topics": [],          # [{id,name,keywords:[],articleIds:[]}]
        "tags": {},            # {tag: [articleId,...]}  倒排索引（由 analysis.tags 派生）
        "collections": {},     # {name: {name,articleIds:[],breakdown:{},updatedAt}}
        "articles": {},        # {id: {...}}
    }


def load_kb(path=KB_PATH):
    if not os.path.exists(path):
        return empty_kb()
    try:
        with open(path, "r", encoding="utf-8") as f:
            kb = json.load(f)
    except (ValueError, OSError) as e:
        print(f"⚠️  读取知识库失败（{e}），将以空库继续。", file=sys.stderr)
        return empty_kb()
    # 兜底补全缺失的顶层字段
    base = empty_kb()
    for k, v in base.items():
        kb.setdefault(k, v)
    return kb


def save_kb(kb, path=KB_PATH):
    kb["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ════════════════════════════════════════════════════════════════
#  规范化文章记录
# ════════════════════════════════════════════════════════════════

def _empty_analysis():
    return {"summary": "", "viewpoints": [], "data": [], "tags": [], "audience": ""}


def normalize_article(raw):
    """把采集到的一条原始记录规整为 KB article 结构。"""
    link = str(raw.get("link") or raw.get("url") or "").strip()
    title = str(raw.get("title") or "").strip()
    aid = str(raw.get("id") or "").strip() or canonical_id(link, title)
    return {
        "id": aid,
        "account": str(raw.get("account") or "").strip(),
        "query": str(raw.get("query") or "").strip(),
        "title": title,
        "link": link,
        "cover": str(raw.get("cover") or "").strip(),
        "images": list(raw.get("images") or []),
        "publishDate": str(raw.get("publishDate") or "").strip(),
        "digest": str(raw.get("digest") or raw.get("summary") or "").strip(),
        "content": str(raw.get("content") or "").strip(),
        "collectedAt": str(raw.get("collectedAt") or "").strip(),
        "ingestedAt": datetime.now().isoformat(timespec="seconds"),
        "analyzedAt": None,
        "analysis": _empty_analysis(),
        "topicIds": [],
        "crossRefs": [],
    }


# ════════════════════════════════════════════════════════════════
#  入库（去重合并）
# ════════════════════════════════════════════════════════════════

# 合并时「补全但不覆盖已有」的内容字段；已有 analysis / 结构字段一律保留
_CONTENT_FIELDS = ("account", "query", "title", "link", "cover", "images",
                   "publishDate", "digest", "content", "collectedAt")


def ingest_articles(kb, articles):
    added = updated = 0
    for raw in articles:
        rec = normalize_article(raw)
        aid = rec["id"]
        cur = kb["articles"].get(aid)
        if cur is None:
            kb["articles"][aid] = rec
            added += 1
        else:
            # 已存在：用新数据补全缺失/更长的内容字段，但绝不动 analysis / 结构
            changed = False
            for fld in _CONTENT_FIELDS:
                new_v = rec.get(fld)
                old_v = cur.get(fld)
                if fld == "images":
                    if new_v and len(new_v) > len(old_v or []):
                        cur[fld] = new_v
                        changed = True
                elif fld == "content":
                    # 正文取更长的那份（重抓可能补到更完整正文）
                    if new_v and len(new_v) > len(old_v or ""):
                        cur[fld] = new_v
                        changed = True
                elif new_v and not old_v:
                    cur[fld] = new_v
                    changed = True
            if changed:
                updated += 1
    rebuild_tags(kb)
    rebuild_topic_members(kb)
    return added, updated


# ════════════════════════════════════════════════════════════════
#  索引重建
# ════════════════════════════════════════════════════════════════

def rebuild_tags(kb):
    """从每篇 analysis.tags 重建标签倒排索引。"""
    tags = {}
    for aid, a in kb["articles"].items():
        for t in (a.get("analysis") or {}).get("tags") or []:
            t = str(t).strip()
            if not t:
                continue
            tags.setdefault(t, [])
            if aid not in tags[t]:
                tags[t].append(aid)
    kb["tags"] = dict(sorted(tags.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def rebuild_topic_members(kb):
    """根据每篇 topicIds 重建 topics[].articleIds（双向一致）。"""
    by_topic = {}
    for aid, a in kb["articles"].items():
        for tid in a.get("topicIds") or []:
            by_topic.setdefault(tid, []).append(aid)
    for t in kb["topics"]:
        t["articleIds"] = by_topic.get(t["id"], [])


def upsert_topic(kb, tid, name=None, keywords=None):
    tid = str(tid).strip()
    for t in kb["topics"]:
        if t["id"] == tid:
            if name is not None:
                t["name"] = name
            if keywords is not None:
                t["keywords"] = keywords
            return t
    t = {"id": tid, "name": name or tid, "keywords": keywords or [], "articleIds": []}
    kb["topics"].append(t)
    return t


# ════════════════════════════════════════════════════════════════
#  写回分析（agent 主路径）
# ════════════════════════════════════════════════════════════════

def set_analysis(kb, aid, analysis):
    a = kb["articles"].get(aid)
    if a is None:
        raise KeyError(aid)
    merged = _empty_analysis()
    merged.update({k: analysis[k] for k in ANALYSIS_FIELDS if k in analysis})
    a["analysis"] = merged
    a["analyzedAt"] = datetime.now().isoformat(timespec="seconds")
    return a


def set_meta(kb, aid, topics=None, tags=None, crossrefs=None, add=False):
    a = kb["articles"].get(aid)
    if a is None:
        raise KeyError(aid)
    if topics is not None:
        a["topicIds"] = sorted(set(a["topicIds"]) | set(topics)) if add else list(topics)
    if crossrefs is not None:
        a["crossRefs"] = sorted(set(a["crossRefs"]) | set(crossrefs)) if add else list(crossrefs)
    if tags is not None:
        cur = a.setdefault("analysis", _empty_analysis())
        cur["tags"] = sorted(set(cur.get("tags") or []) | set(tags)) if add else list(tags)
    return a


def apply_batch(kb, batch):
    """
    一次写回一批（agent 工作流的核心入口）。batch 形如：
    {
      "topics":   [{"id","name","keywords":[]}],          # 可选，先 upsert
      "collections": {name: {...}},                       # 可选
      "articles": {
        "<id>": {"analysis": {...}, "topicIds": [...], "tags": [...], "crossRefs": [...]}
      }
    }
    """
    counts = {"topics": 0, "analysis": 0, "meta": 0, "collections": 0, "missing": []}
    for t in batch.get("topics") or []:
        upsert_topic(kb, t.get("id"), t.get("name"), t.get("keywords"))
        counts["topics"] += 1
    for aid, payload in (batch.get("articles") or {}).items():
        if aid not in kb["articles"]:
            counts["missing"].append(aid)
            continue
        if "analysis" in payload and payload["analysis"]:
            set_analysis(kb, aid, payload["analysis"])
            counts["analysis"] += 1
        topics = payload.get("topicIds", payload.get("topics"))
        tags = payload.get("tags")
        crossrefs = payload.get("crossRefs", payload.get("crossrefs"))
        if topics is not None or tags is not None or crossrefs is not None:
            set_meta(kb, aid, topics=topics, tags=tags, crossrefs=crossrefs,
                     add=bool(payload.get("add")))
            counts["meta"] += 1
    if batch.get("collections"):
        import_collections(kb, batch["collections"])
        counts["collections"] = len(batch["collections"])
    rebuild_tags(kb)
    rebuild_topic_members(kb)
    return counts


def import_collections(kb, collections):
    """
    吸收离线页导出的收藏夹。collections 形如：
      {"收藏夹名": {"name","articleIds":[...],"breakdown":{...}}}
    或离线页导出的 {"collections": {...}} —— 两种都接受。
    """
    if isinstance(collections, dict) and "collections" in collections:
        collections = collections["collections"]
    for name, c in (collections or {}).items():
        cur = kb["collections"].get(name, {"name": name, "articleIds": [], "breakdown": {}})
        ids = c.get("articleIds") or c.get("ids") or []
        cur["name"] = c.get("name", name)
        cur["articleIds"] = list(dict.fromkeys(ids))   # 去重保序
        if c.get("breakdown"):
            cur["breakdown"] = c["breakdown"]
        cur["updatedAt"] = datetime.now().isoformat(timespec="seconds")
        kb["collections"][name] = cur
    return len(collections or {})


def replace_collections(kb, incoming):
    """以传入集合为准**整体替换**收藏夹（用于离线页自动保存：能反映删除）。

    保留各收藏夹已有的 breakdown（agent 写回的深度拆解），只更新成员与名称。
    """
    if isinstance(incoming, dict) and "collections" in incoming:
        incoming = incoming["collections"]
    now = datetime.now().isoformat(timespec="seconds")
    new = {}
    for name, c in (incoming or {}).items():
        old = kb["collections"].get(name, {})
        ids = c.get("articleIds") or c.get("ids") or []
        new[name] = {
            "name": c.get("name", name),
            "articleIds": list(dict.fromkeys(ids)),
            "breakdown": old.get("breakdown", {}),
            "updatedAt": now,
        }
    kb["collections"] = new
    return len(new)


# ════════════════════════════════════════════════════════════════
#  统计 / 检索
# ════════════════════════════════════════════════════════════════

def compute_stats(kb):
    arts = list(kb["articles"].values())
    n = len(arts)
    analyzed = sum(1 for a in arts if a.get("analyzedAt"))
    accounts = {}
    dates = []
    chars = 0
    for a in arts:
        accounts[a["account"]] = accounts.get(a["account"], 0) + 1
        if a.get("publishDate"):
            dates.append(a["publishDate"])
        chars += len(a.get("content") or "")
    dates.sort()
    top_tags = [(t, len(ids)) for t, ids in kb["tags"].items()][:15]
    return {
        "articles": n,
        "analyzed": analyzed,
        "analyzedPct": round(analyzed / n * 100, 1) if n else 0.0,
        "accounts": dict(sorted(accounts.items(), key=lambda kv: -kv[1])),
        "dateRange": [dates[0], dates[-1]] if dates else [None, None],
        "totalChars": chars,
        "topics": [{"id": t["id"], "name": t["name"], "count": len(t["articleIds"])}
                   for t in kb["topics"]],
        "tagCount": len(kb["tags"]),
        "topTags": top_tags,
        "collections": {name: len(c.get("articleIds") or [])
                        for name, c in kb["collections"].items()},
        "updatedAt": kb.get("updatedAt"),
    }


def query_articles(kb, account=None, topic=None, tag=None,
                   unanalyzed=False, analyzed=False, limit=None):
    out = []
    for a in kb["articles"].values():
        if account and a["account"] != account:
            continue
        if topic and topic not in (a.get("topicIds") or []):
            continue
        if tag and tag not in ((a.get("analysis") or {}).get("tags") or []):
            continue
        if unanalyzed and a.get("analyzedAt"):
            continue
        if analyzed and not a.get("analyzedAt"):
            continue
        out.append(a)
    # 默认按发布日期倒序
    out.sort(key=lambda a: a.get("publishDate") or "", reverse=True)
    if limit:
        out = out[:limit]
    return out


def _slim(a, with_content=False):
    """检索输出的精简视图（默认不含全文，避免刷屏）。"""
    d = {
        "id": a["id"], "account": a["account"], "title": a["title"],
        "publishDate": a["publishDate"], "link": a["link"],
        "analyzed": bool(a.get("analyzedAt")),
        "tags": (a.get("analysis") or {}).get("tags") or [],
        "topicIds": a.get("topicIds") or [],
        "chars": len(a.get("content") or ""),
    }
    if with_content:
        d["digest"] = a.get("digest") or ""
        d["content"] = a.get("content") or ""
    return d


# ════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════

def _read_json_arg(args):
    """从 --file / --stdin / 位置 json 串里取一个 JSON 对象。"""
    if getattr(args, "file", None):
        with open(args.file, "r", encoding="utf-8") as f:
            return json.load(f)
    if getattr(args, "stdin", False):
        return json.load(sys.stdin)
    if getattr(args, "json", None) and isinstance(args.json, str):
        return json.loads(args.json)
    raise SystemExit("请用 --file <路径> 或 --stdin 提供 JSON。")


def cmd_ingest(args):
    kb = load_kb(args.kb)
    with open(args.path, "r", encoding="utf-8") as f:
        data = json.load(f)
    articles = data.get("articles") if isinstance(data, dict) else data
    if not isinstance(articles, list):
        raise SystemExit("输入文件里没有 articles 数组。")
    added, updated = ingest_articles(kb, articles)
    save_kb(kb, args.kb)
    print(f"✓ 入库完成：新增 {added} 篇，补全 {updated} 篇，库内共 {len(kb['articles'])} 篇。")


def cmd_stats(args):
    kb = load_kb(args.kb)
    s = compute_stats(kb)
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    print("═" * 56)
    print("  墨摘 · 知识库统计")
    print("═" * 56)
    print(f"  文章总数 : {s['articles']}")
    print(f"  已分析   : {s['analyzed']} / {s['articles']}  ({s['analyzedPct']}%)")
    rng = s["dateRange"]
    print(f"  时间范围 : {rng[0] or '—'} ~ {rng[1] or '—'}")
    print(f"  正文总字 : {s['totalChars']:,}")
    print(f"  主题数   : {len(s['topics'])}   标签数 : {s['tagCount']}")
    if s["accounts"]:
        print("  公众号   :")
        for acc, c in list(s["accounts"].items())[:12]:
            print(f"     - {acc or '(未知)'}: {c}")
    if s["topics"]:
        print("  主题分布 :")
        for t in s["topics"]:
            print(f"     · {t['name']} ({t['count']})")
    if s["topTags"]:
        print("  高频标签 : " + "  ".join(f"{t}×{n}" for t, n in s["topTags"]))
    if s["collections"]:
        print("  收藏夹   : " + "  ".join(f"{n}({c})" for n, c in s["collections"].items()))
    print(f"  更新于   : {s['updatedAt'] or '—'}")
    print("═" * 56)


def cmd_list(args):
    kb = load_kb(args.kb)
    arts = query_articles(kb, account=args.account, topic=args.topic, tag=args.tag,
                          unanalyzed=args.unanalyzed, analyzed=args.analyzed,
                          limit=args.limit)
    if args.json:
        print(json.dumps([_slim(a, with_content=args.content) for a in arts],
                         ensure_ascii=False, indent=2))
        return
    if not arts:
        print("（无匹配文章）")
        return
    for a in arts:
        flag = "✓" if a.get("analyzedAt") else "·"
        print(f"  {flag} [{a['id'][:10]}] {a.get('publishDate') or '----------'}  "
              f"{a['account'][:10]:<10}  {a['title'][:40]}")
    print(f"\n  共 {len(arts)} 篇（{'含' if args.content else '不含'}正文）。")


def cmd_get(args):
    kb = load_kb(args.kb)
    a = kb["articles"].get(args.id)
    if not a:
        raise SystemExit(f"未找到文章 id={args.id}")
    print(json.dumps(a, ensure_ascii=False, indent=2))


def cmd_apply(args):
    kb = load_kb(args.kb)
    batch = _read_json_arg(args)
    counts = apply_batch(kb, batch)
    save_kb(kb, args.kb)
    msg = (f"✓ 写回：分析 {counts['analysis']} 篇，结构 {counts['meta']} 篇，"
           f"主题 {counts['topics']} 个，收藏夹 {counts['collections']} 个。")
    if counts["missing"]:
        msg += f"\n⚠️  库中不存在以下 id（已跳过）：{', '.join(counts['missing'][:8])}" \
               + (" …" if len(counts['missing']) > 8 else "")
    print(msg)


def cmd_set_analysis(args):
    kb = load_kb(args.kb)
    analysis = _read_json_arg(args)
    try:
        set_analysis(kb, args.id, analysis)
    except KeyError:
        raise SystemExit(f"未找到文章 id={args.id}")
    rebuild_tags(kb)
    save_kb(kb, args.kb)
    print(f"✓ 已写回 {args.id} 的分析。")


def _csv(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def cmd_set_meta(args):
    kb = load_kb(args.kb)
    try:
        set_meta(kb, args.id,
                 topics=_csv(args.topics) if args.topics is not None else None,
                 tags=_csv(args.tags) if args.tags is not None else None,
                 crossrefs=_csv(args.crossrefs) if args.crossrefs is not None else None,
                 add=args.add)
    except KeyError:
        raise SystemExit(f"未找到文章 id={args.id}")
    rebuild_tags(kb)
    rebuild_topic_members(kb)
    save_kb(kb, args.kb)
    print(f"✓ 已更新 {args.id} 的结构信息。")


def cmd_topic_upsert(args):
    kb = load_kb(args.kb)
    upsert_topic(kb, args.id, name=args.name, keywords=_csv(args.keywords))
    rebuild_topic_members(kb)
    save_kb(kb, args.kb)
    print(f"✓ 主题 {args.id}（{args.name or args.id}）已就绪。")


def cmd_import_collections(args):
    kb = load_kb(args.kb)
    with open(args.path, "r", encoding="utf-8") as f:
        data = json.load(f)
    n = import_collections(kb, data)
    save_kb(kb, args.kb)
    print(f"✓ 已导入 {n} 个收藏夹。")


def cmd_export_html(args):
    # 延迟导入，避免无渲染需求时的耦合
    try:
        import render_html
    except ImportError:
        raise SystemExit("未找到 render_html.py（应与 kb.py 同目录）。")
    out = render_html.render(kb_path=args.kb, out_path=args.out)
    print(f"✓ 已生成离线工作台：{out}")
    print("  用浏览器打开它即可（双击或 file:// 直接访问）。")


def cmd_serve(args):
    """本地起一个 http 服务托管 digest.html，并让页面里的收藏夹自动存回知识库。

    这是离线单文件的「能真正保存」方案：file:// 页面不能写盘，但从 http://127.0.0.1
    打开的页面可以把收藏夹 POST 回来，落到 knowledge_base.json——清缓存/换浏览器都不丢。
    仅监听本机回环地址，不对外暴露。
    """
    import http.server
    import socketserver
    from urllib.parse import urlparse

    kb_path = args.kb
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    digest = os.path.join(OUTPUT_DIR, "digest.html")
    if not os.path.exists(digest):
        try:
            import render_html
            render_html.render(kb_path=kb_path)
        except Exception as e:
            print(f"⚠️  自动渲染 digest.html 失败：{e}")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=OUTPUT_DIR, **k)

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/digest.html")
                self.end_headers()
                return
            if path == "/api/kb-collections":
                kb = load_kb(kb_path)
                return self._json({"collections": kb.get("collections", {})})
            return super().do_GET()

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/save-collections":
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    data = json.loads(self.rfile.read(n) or b"{}")
                except (ValueError, TypeError):
                    return self._json({"error": "bad json"}, 400)
                kb = load_kb(kb_path)
                count = replace_collections(kb, data)
                save_kb(kb, kb_path)
                return self._json({"ok": True, "count": count})
            return self._json({"error": "not found"}, 404)

        def log_message(self, *a):
            pass  # 安静

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.port), Handler) as httpd:
        url = f"http://127.0.0.1:{args.port}/digest.html"
        print(f"✓ 本地服务已启动：{url}")
        print("  在该页面建/改收藏夹会自动保存到 knowledge_base.json（清缓存、换浏览器都不丢）。")
        print("  Ctrl+C 停止。")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止。")


def cmd_rebuild(args):
    kb = load_kb(args.kb)
    rebuild_tags(kb)
    rebuild_topic_members(kb)
    save_kb(kb, args.kb)
    print("✓ 索引已重建（标签倒排 + 主题成员）。")


def build_parser():
    p = argparse.ArgumentParser(
        prog="kb.py", description="墨摘 · 微信公众号知识库（持续累积 + 结构性分析载体）")
    p.add_argument("--kb", default=KB_PATH, help=f"知识库路径（默认 {os.path.relpath(KB_PATH, SCRIPT_DIR)}）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ingest", help="把 articles_*.json 去重合并入库")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("stats", help="统计概览")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("list", help="检索文章（供 agent 取批次）")
    sp.add_argument("--account")
    sp.add_argument("--topic")
    sp.add_argument("--tag")
    sp.add_argument("--unanalyzed", action="store_true", help="只看未分析")
    sp.add_argument("--analyzed", action="store_true", help="只看已分析")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--content", action="store_true", help="JSON 输出里附正文（供 agent 拆解）")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("get", help="按 id 打印单篇完整 JSON")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("apply", help="一次写回一批分析/结构（agent 主路径）")
    sp.add_argument("--file")
    sp.add_argument("--stdin", action="store_true")
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("set-analysis", help="写回单篇五段式分析")
    sp.add_argument("id")
    sp.add_argument("--file")
    sp.add_argument("--stdin", action="store_true")
    sp.add_argument("--json", help="直接传 JSON 字符串")
    sp.set_defaults(func=cmd_set_analysis)

    sp = sub.add_parser("set-meta", help="写回单篇结构（主题/标签/交叉引用）")
    sp.add_argument("id")
    sp.add_argument("--topics", help="逗号分隔 topicId")
    sp.add_argument("--tags", help="逗号分隔标签")
    sp.add_argument("--crossrefs", help="逗号分隔关联文章 id")
    sp.add_argument("--add", action="store_true", help="追加而非覆盖")
    sp.set_defaults(func=cmd_set_meta)

    sp = sub.add_parser("topic-upsert", help="新建/更新一个主题")
    sp.add_argument("id")
    sp.add_argument("--name")
    sp.add_argument("--keywords", help="逗号分隔关键词")
    sp.set_defaults(func=cmd_topic_upsert)

    sp = sub.add_parser("import-collections", help="导入离线页导出的收藏夹 JSON")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_import_collections)

    sp = sub.add_parser("export-html", help="生成离线 HTML 工作台")
    sp.add_argument("--out", help="输出路径（默认 output/digest.html）")
    sp.set_defaults(func=cmd_export_html)

    sp = sub.add_parser("serve", help="本地起服务托管 digest.html，收藏夹自动存回知识库（清缓存不丢）")
    sp.add_argument("--port", type=int, default=8765)
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("rebuild", help="重建索引（标签倒排 + 主题成员）")
    sp.set_defaults(func=cmd_rebuild)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)

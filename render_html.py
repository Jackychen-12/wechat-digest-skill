#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════════════════
 墨摘 · 离线 HTML 工作台生成器 (wechat-digest skill)
 ─────────────────────────────────────────────────────────────────────────
 把 output/knowledge_base.json 内联进 assets/digest_template.html，产出一个
 **自包含、可双击打开**的离线工作台 output/digest.html：
   · 总览（篇数/已分析/账号/主题/标签）
   · 文章库（筛选 + ⭐ 建收藏夹 + 逐篇五段式拆解）
   · 知识库（主题聚类 / 标签云 / 时间线 / 交叉引用）
   · 收藏夹（建夹 + 收藏夹拆解 + 导出 JSON）
 数据 / 样式 / 脚本全部内嵌，无需后端、无需联网即可浏览。
 「预留接口」：页面设置里可填后端 URL + provider/model + 本地 Key，按现有
 /api/chat 协议做按需 AI 拆解；不填则只读 agent 预生成的分析。

 纯标准库实现。
   python3 render_html.py                 # 用默认 KB 生成默认 digest.html
   python3 render_html.py --kb x.json --out y.html
════════════════════════════════════════════════════════════════════════════
"""

import argparse
import json
import os
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
KB_PATH = os.path.join(OUTPUT_DIR, "knowledge_base.json")
TEMPLATE_PATH = os.path.join(SCRIPT_DIR, "assets", "digest_template.html")
DEFAULT_OUT = os.path.join(OUTPUT_DIR, "digest.html")

DATA_PLACEHOLDER = "__KB_DATA__"
GENERATED_PLACEHOLDER = "__GENERATED_AT__"


def _load_kb(kb_path):
    if not os.path.exists(kb_path):
        return {"version": 1, "updatedAt": None, "topics": [], "tags": {},
                "collections": {}, "articles": {}}
    with open(kb_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _embed(data_json):
    """让 JSON 安全嵌入 <script type="application/json"> 标签内。

    数据放在 application/json 脚本块里、由 JSON.parse 读取，因此只需防止
    `</script>` 提前闭合标签：把所有 `</` 转义为 `<\\/`（JSON.parse 仍能解析）。
    """
    return data_json.replace("</", "<\\/")


def render(kb_path=KB_PATH, out_path=None):
    out_path = out_path or DEFAULT_OUT
    if not os.path.exists(TEMPLATE_PATH):
        raise SystemExit(f"未找到模板 {TEMPLATE_PATH}")
    kb = _load_kb(kb_path)
    template = open(TEMPLATE_PATH, "r", encoding="utf-8").read()

    data_json = _embed(json.dumps(kb, ensure_ascii=False))
    html = template.replace(DATA_PLACEHOLDER, data_json)
    html = html.replace(GENERATED_PLACEHOLDER,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, out_path)
    return out_path


def main():
    p = argparse.ArgumentParser(prog="render_html.py", description="墨摘 · 离线 HTML 工作台生成器")
    p.add_argument("--kb", default=KB_PATH)
    p.add_argument("--out", default=DEFAULT_OUT)
    args = p.parse_args()
    out = render(kb_path=args.kb, out_path=args.out)
    n = len(_load_kb(args.kb).get("articles", {}))
    print(f"✓ 已生成离线工作台：{out}（{n} 篇）")
    print("  用浏览器打开它即可（双击或 file:// 直接访问）。")


if __name__ == "__main__":
    main()

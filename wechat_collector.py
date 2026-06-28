#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════════════════
 墨摘 · 微信公众号采集脚本 (wechat-digest skill) —— P0 全文挖掘与获取
 ─────────────────────────────────────────────────────────────────────────
 以「微信公众平台后台」的 Cookie+Token 接口为核心采集公众号文章：
   1) searchbiz  按公众号名称搜到 fakeid
   2) appmsg     按 fakeid 翻页拉取文章列表（标题/链接/摘要/发布时间/封面）
   3) 抓取每篇正文并清洗为纯文本，同时记录正文内图片 URL（全文挖掘）
   4) 落盘 output/articles_YYYYMMDD.json（供分析）+ index_YYYYMMDD.xlsx
   5) 默认自动「入库」到 output/knowledge_base.json（持续累积知识库）

 这是公众号无公开 API 时最稳定的采集路径，但仍受微信频控约束——脚本内置
 随机延时、指数退避、频控/凭证失效识别与进度保存，尽量稳。

 ── 命令（对标 redbook skill 的 search / read / whoami）──
   python3 wechat_collector.py collect 晚点LatePost --since 2025-01-01 --count 10
   python3 wechat_collector.py read  https://mp.weixin.qq.com/s/xxxx
   python3 wechat_collector.py whoami
   python3 wechat_collector.py                # 无参数 → 读文件顶部默认配置（旧用法）

 ⚠️ 阅读数/在看/点赞需微信客户端签名参数，后台接口拿不到，故不含互动量。
 ⚠️ token/cookie 会过期，采集失败优先重新获取（见 SKILL.md）。
════════════════════════════════════════════════════════════════════════════
"""

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime

import requests

# ════════════════════════════════════════════════════════════════
#  默认配置区（仅「无参数运行」时生效；推荐用命令行子命令）
# ════════════════════════════════════════════════════════════════

ACCOUNTS = [
    "晚点LatePost",
]
DATE_FILTER_THRESHOLD = "2025-01-01"   # 仅采此日期(含)之后的文章
TARGET_COUNT = 10                      # 每个号目标篇数
FETCH_CONTENT = True                   # 是否抓正文
AUTO_INGEST = True                     # 采完是否自动入知识库

# 输出目录
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# ── 反爬节流参数（频控很敏感，宁慢勿快）──
REQUEST_DELAY_MIN = 3.0
REQUEST_DELAY_MAX = 6.0
CONTENT_DELAY_MIN = 1.5
CONTENT_DELAY_MAX = 3.5
FREQ_COOLDOWN = 60
MAX_FREQ_RETRIES = 2
LIST_PAGE_SIZE = 5
MAX_LIST_PAGES = 40
CONTENT_RETRIES = 3

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

MP_BASE = "https://mp.weixin.qq.com"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "credentials.json")


# ════════════════════════════════════════════════════════════════
#  自定义异常
# ════════════════════════════════════════════════════════════════

class AuthError(Exception):
    """token / cookie 失效或未登录。"""


class FreqControlError(Exception):
    """命中微信频控，多次重试仍失败。"""


class ApiError(Exception):
    """接口返回非 0 的其它错误。"""


# ════════════════════════════════════════════════════════════════
#  规范化 id（与 kb.py.canonical_id 一致；优先复用 kb 模块）
# ════════════════════════════════════════════════════════════════

try:
    from kb import canonical_id  # type: ignore
except Exception:  # 容错：kb.py 缺失时本地降级
    import hashlib

    def canonical_id(link="", title=""):
        link = (link or "").strip()
        if link:
            m = re.search(r"[?&]sn=([0-9a-fA-F]+)", link)
            if m:
                return m.group(1).lower()
            return "h" + hashlib.sha1(link.encode("utf-8")).hexdigest()[:16]
        if title:
            return "t" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]
        return "x" + hashlib.sha1(str(datetime.now()).encode("utf-8")).hexdigest()[:16]


# ════════════════════════════════════════════════════════════════
#  凭证加载
# ════════════════════════════════════════════════════════════════

def load_credentials():
    """优先读 credentials.json，其次环境变量 WECHAT_TOKEN / WECHAT_COOKIE。"""
    token = cookie = ""
    if os.path.exists(CREDENTIALS_FILE):
        try:
            with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            token = str(data.get("token", "")).strip()
            cookie = str(data.get("cookie", "")).strip()
        except Exception as e:
            print(f"⚠️  读取 credentials.json 失败：{e}")
    token = token or os.environ.get("WECHAT_TOKEN", "").strip()
    cookie = cookie or os.environ.get("WECHAT_COOKIE", "").strip()
    if token in ("在此粘贴 token 数字", "在此粘贴token数字"):
        token = ""
    if cookie in ("在此粘贴完整 cookie 字符串", "在此粘贴完整cookie字符串"):
        cookie = ""
    return token, cookie


def print_credential_guide():
    print("""
╭───────────────────────────────────────────────────────────────╮
│  未检测到有效的 token / cookie，无法采集。请按下面三步获取：     │
╰───────────────────────────────────────────────────────────────╯
  1. 浏览器登录 https://mp.weixin.qq.com （个人订阅号即可，免费）
  2. 登录后看地址栏 URL，形如：
       https://mp.weixin.qq.com/cgi-bin/home?...&token=1234567890
     末尾 token= 后的那串数字，就是 token。
  3. 按 F12 → Network 面板 → 刷新页面 → 点任意一个 mp.weixin.qq.com 的请求
     → Request Headers 里找到 Cookie，复制整条（很长一串）。

  然后把这两个值填进 skill/credentials.json：
       cp credentials.example.json credentials.json
       # 编辑 credentials.json，填入 token 和 cookie

  （或设置环境变量 WECHAT_TOKEN / WECHAT_COOKIE）

  ⚠️ token/cookie 会过期（通常几小时~几天），失效后重新获取即可。
""")


def make_session(cookie):
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cookie": cookie,
        "Referer": MP_BASE + "/cgi-bin/appmsg",
    })
    return s


# ════════════════════════════════════════════════════════════════
#  正文清洗（移植自 backend/main.ts 的 htmlToText / sliceBetween / decodeEntities）
# ════════════════════════════════════════════════════════════════

def decode_entities(s):
    s = s or ""
    s = (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
          .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " "))
    return s.strip()


def html_to_text(html):
    """把一段 HTML 转为带换行的纯文本（对标 main.ts 的 htmlToText）。"""
    if not html:
        return ""
    html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", "", html, flags=re.I)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</(p|div|section|li|h[1-6])>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", "", html)
    html = (html.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'"))
    html = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), html)
    html = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), html)
    html = re.sub(r"[ \t]+\n", "\n", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def slice_between(html, start_re, end_re):
    """截取 start 正则之后、end 正则之前的片段（对标 main.ts 的 sliceBetween）。"""
    start_m = re.search(start_re, html, flags=re.I)
    if not start_m:
        return ""
    rest = html[start_m.end():]
    end_m = re.search(end_re, rest, flags=re.I)
    end_idx = end_m.start() if end_m else len(rest)
    return rest[:end_idx]


def slice_content_html(html):
    """取出 mp 文章页正文 div 的内层 HTML（未清洗，供取文本+图片）。"""
    return (
        slice_between(html, r'<div[^>]*id="js_content"[^>]*>',
                      r'</div>\s*(?:<script|<div[^>]*id="js_tags")')
        or slice_between(html, r'<div[^>]*id="js_content"[^>]*>', r'</div>')
        or slice_between(html, r'<div[^>]*class="rich_media_content"[^>]*>', r'</div>\s*<script')
    )


def extract_images(content_html):
    """从正文 HTML 提取图片 URL（微信图片懒加载在 data-src，常规在 src）。"""
    if not content_html:
        return []
    urls = []
    for m in re.finditer(r'<img[^>]+>', content_html, flags=re.I):
        tag = m.group(0)
        src = (re.search(r'data-src="([^"]+)"', tag, flags=re.I)
               or re.search(r"data-src='([^']+)'", tag, flags=re.I)
               or re.search(r'\ssrc="([^"]+)"', tag, flags=re.I)
               or re.search(r"\ssrc='([^']+)'", tag, flags=re.I))
        if not src:
            continue
        u = decode_entities(src.group(1)).replace("&amp;", "&")
        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("http") and u not in urls:
            urls.append(u)
    return urls


def pick(html, re_):
    m = re.search(re_, html, flags=re.I)
    return decode_entities(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""


def is_blocked_page(html):
    """文章页触发风控/验证（粗判）。"""
    if not html:
        return False
    return bool(re.search(r"请输入验证码|antispider|环境异常|访问过于频繁", html)) \
        and "js_content" not in html


# ════════════════════════════════════════════════════════════════
#  节流 & HTTP 工具
# ════════════════════════════════════════════════════════════════

def polite_sleep(lo, hi):
    time.sleep(random.uniform(lo, hi))


def api_get(session, token, path, extra_params):
    """调用 mp 后台 JSON 接口，带频控/凭证识别与重试。"""
    params = {"token": token, "lang": "zh_CN", "f": "json", "ajax": "1"}
    params.update(extra_params)
    url = MP_BASE + path

    for attempt in range(MAX_FREQ_RETRIES + 1):
        polite_sleep(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
        try:
            r = session.get(url, params=params, timeout=20)
        except requests.RequestException as e:
            if attempt < MAX_FREQ_RETRIES:
                time.sleep(2 ** attempt + random.random())
                continue
            raise ApiError(f"网络请求失败：{e}")

        try:
            data = r.json()
        except ValueError:
            raise AuthError("接口未返回 JSON（通常是 token/cookie 失效，被重定向到登录页）。")

        ret = (data.get("base_resp") or {}).get("ret", 0)
        err = (data.get("base_resp") or {}).get("err_msg", "")

        if ret == 0:
            return data
        if ret == 200013:
            if attempt < MAX_FREQ_RETRIES:
                print(f"   ⏳ 命中频控(ret=200013)，冷却 {FREQ_COOLDOWN}s 后重试"
                      f"（第 {attempt + 1}/{MAX_FREQ_RETRIES} 次）…")
                time.sleep(FREQ_COOLDOWN)
                continue
            raise FreqControlError("多次重试仍被频控，已停止。请稍后（建议隔几十分钟）再跑。")
        if ret in (200003, 200002, -3) or "session" in str(err).lower() or "token" in str(err).lower():
            raise AuthError(f"凭证失效或无权限（ret={ret}, err={err}）。请重新登录获取 token+cookie。")
        raise ApiError(f"接口错误 ret={ret}, err={err}")

    raise ApiError("未知错误")


def fetch_article(url):
    """
    抓取并清洗单篇正文，返回 dict：{content, images, ogCover}。
    失败时 content 为空串（不影响整体）。
    """
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": MP_BASE + "/",
    }
    last = {"content": "", "images": [], "ogCover": ""}
    for attempt in range(CONTENT_RETRIES):
        polite_sleep(CONTENT_DELAY_MIN, CONTENT_DELAY_MAX)
        try:
            r = requests.get(url, headers=headers, timeout=20)
            html = r.text or ""
            if is_blocked_page(html):
                last = {"content": "__blocked__", "images": [], "ogCover": ""}
            else:
                content_html = slice_content_html(html)
                body = html_to_text(content_html) if content_html else ""
                # 正文兜底：取不到 div 时退回 og:description
                if not body or len(body) < 20:
                    og = pick(html, r'<meta property="og:description" content="([^"]*)"')
                    if og and len(og) > len(body):
                        body = og
                images = extract_images(content_html)
                og_cover = pick(html, r'<meta property="og:image" content="([^"]*)"')
                if body and len(body) >= 20:
                    return {"content": body, "images": images, "ogCover": og_cover}
                last = {"content": body, "images": images, "ogCover": og_cover}
        except requests.RequestException:
            pass
        if attempt < CONTENT_RETRIES - 1:
            time.sleep(1.5 * (2 ** attempt) + random.random())
    if last.get("content") == "__blocked__":
        last["content"] = ""
    return last


# ════════════════════════════════════════════════════════════════
#  采集逻辑
# ════════════════════════════════════════════════════════════════

def search_biz(session, token, name):
    """按名称搜公众号，返回 (fakeid, nickname)。找不到抛 ApiError。"""
    data = api_get(session, token, "/cgi-bin/searchbiz",
                   {"action": "search_biz", "begin": "0", "count": "5", "query": name})
    lst = data.get("list") or []
    if not lst:
        raise ApiError(f"未搜到公众号「{name}」（请确认名称是否精确）。")
    exact = next((b for b in lst if b.get("nickname") == name), None)
    chosen = exact or lst[0]
    if len(lst) > 1:
        print(f"   ℹ️  「{name}」匹配到 {len(lst)} 个号，已选用："
              f"{chosen.get('nickname')}（如需其它请调整名称）：")
        for b in lst[:5]:
            mark = "→" if b is chosen else " "
            print(f"      {mark} {b.get('nickname')}  (fakeid={b.get('fakeid')})")
    return chosen.get("fakeid"), chosen.get("nickname", name)


def unix_to_date(ts):
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return ""


def collect_account(session, token, name, since, count, fetch_content):
    """采集单个公众号，返回文章记录列表。"""
    print(f"\n📰 采集公众号：{name}")
    fakeid, nickname = search_biz(session, token, name)
    print(f"   ✓ fakeid={fakeid}  昵称={nickname}")

    collected = []
    begin = 0
    pages = 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    while len(collected) < count and pages < MAX_LIST_PAGES:
        data = api_get(session, token, "/cgi-bin/appmsg", {
            "action": "list_ex", "begin": str(begin), "count": str(LIST_PAGE_SIZE),
            "fakeid": fakeid, "type": "9", "query": "",
        })
        items = data.get("app_msg_list") or []
        total = data.get("app_msg_cnt", 0)
        if not items:
            break

        stop = False
        for it in items:
            date = unix_to_date(it.get("update_time"))
            if date and since and date < since:
                stop = True
                continue
            link = (it.get("link", "") or "").replace("\\/", "/")
            title = decode_entities(it.get("title", ""))
            collected.append({
                "id": canonical_id(link, title),
                "account": nickname,
                "query": name,
                "title": title,
                "digest": decode_entities(it.get("digest", "")),
                "link": link,
                "cover": it.get("cover", ""),
                "images": [],
                "publishDate": date,
                "content": "",
                "collectedAt": now_str,
            })
            print(f"   [{len(collected)}] {date}  {collected[-1]['title'][:36]}")
            if len(collected) >= count:
                break

        pages += 1
        begin += LIST_PAGE_SIZE
        if stop or (total and begin >= total):
            break

    if fetch_content and collected:
        print(f"   ↓ 抓取正文（{len(collected)} 篇）…")
        for i, rec in enumerate(collected, 1):
            if not rec["link"]:
                continue
            res = fetch_article(rec["link"])
            rec["content"] = res["content"]
            rec["images"] = res["images"]
            if not rec["cover"] and res.get("ogCover"):
                rec["cover"] = res["ogCover"]
            flag = (f"{len(res['content'])} 字 / {len(res['images'])} 图"
                    if res["content"] else "未取到(可能图文/视频或风控)")
            print(f"      {i}/{len(collected)} {flag}")

    print(f"   ✓ {name} 共采集 {len(collected)} 篇")
    return collected


# ════════════════════════════════════════════════════════════════
#  落盘 & 入库
# ════════════════════════════════════════════════════════════════

def save_json(articles, accounts, since, partial=False):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    suffix = "_partial" if partial else ""
    path = os.path.join(OUTPUT_DIR, f"articles_{stamp}{suffix}.json")
    payload = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dateFilter": since,
        "accounts": accounts,
        "count": len(articles),
        "partial": partial,
        "articles": articles,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def save_excel(articles, partial=False):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        print("⚠️  未安装 openpyxl，跳过 Excel（pip install openpyxl）。")
        return None

    stamp = datetime.now().strftime("%Y%m%d")
    suffix = "_partial" if partial else ""
    path = os.path.join(OUTPUT_DIR, f"index_{stamp}{suffix}.xlsx")

    wb = Workbook()
    ws = wb.active
    ws.title = "公众号文章索引"
    headers = ["序号", "公众号", "标题", "发布日期", "摘要", "正文字数", "图片数", "链接", "采集时间"]
    ws.append(headers)

    head_fill = PatternFill("solid", fgColor="1F6FEB")
    head_font = Font(bold=True, color="FFFFFF", size=11)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = head_fill
        cell.font = head_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for i, a in enumerate(articles, 1):
        ws.append([
            i, a.get("account", ""), a.get("title", ""), a.get("publishDate", ""),
            a.get("digest", ""), len(a.get("content", "") or ""),
            len(a.get("images", []) or []), a.get("link", ""), a.get("collectedAt", ""),
        ])

    widths = [6, 18, 48, 12, 50, 9, 7, 60, 20]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"

    wb.save(path)
    return path


def ingest_to_kb(articles):
    """采完自动入知识库（失败不影响已落盘的 JSON/Excel）。"""
    try:
        import kb
        store = kb.load_kb()
        added, updated = kb.ingest_articles(store, articles)
        kb.save_kb(store)
        print(f"   ↳ 已入知识库：新增 {added} 篇，补全 {updated} 篇，"
              f"库内共 {len(store['articles'])} 篇。")
        return True
    except Exception as e:
        print(f"   ⚠️ 入库失败（已保留 JSON/Excel，可稍后 `python3 kb.py ingest`）：{e}")
        return False


# ════════════════════════════════════════════════════════════════
#  子命令：search（按关键词搜全网公众号文章）
# ════════════════════════════════════════════════════════════════

def run_search(keyword, count, fetch_content, auto_ingest):
    """按关键词搜索微信公众号文章（跨号），抓取正文并入库。"""
    print("=" * 64)
    print("  墨摘 · 微信公众号文章搜索（关键词 → 跨号采集）")
    print("=" * 64)

    token, cookie = load_credentials()
    if not token or not cookie:
        print_credential_guide()
        return 1

    session = make_session(cookie)
    print(f"\n🔍 搜索关键词：{keyword}  目标 {count} 篇")

    collected = []
    begin = 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    while len(collected) < count and begin < count + 50:
        try:
            data = api_get(session, token, "/cgi-bin/operate_appmsg", {
                "action": "search_article", "begin": str(begin),
                "count": str(min(5, count - len(collected))),
                "query": keyword, "type": "9",
            })
        except AuthError as e:
            print(f"✗ {e}")
            break
        except FreqControlError as e:
            print(f"✗ {e}")
            break
        except ApiError as e:
            print(f"✗ {e}")
            break

        items = data.get("article_list") or data.get("app_msg_list") or []
        if not items:
            # try alternate response key
            items = (data.get("results") or {}).get("list") or []
        if not items:
            print("   (无更多结果)")
            break

        for it in items:
            link = (it.get("link") or it.get("url") or "").replace("\\/", "/")
            title = decode_entities(it.get("title") or "")
            account = decode_entities(it.get("nickname") or it.get("account_name") or "")
            date = ""
            ts = it.get("update_time") or it.get("publish_time") or it.get("create_time")
            if ts:
                date = unix_to_date(ts)
            collected.append({
                "id": canonical_id(link, title),
                "account": account,
                "query": keyword,
                "title": title,
                "digest": decode_entities(it.get("digest") or ""),
                "link": link,
                "cover": it.get("cover") or it.get("image_url") or "",
                "images": [],
                "publishDate": date,
                "content": "",
                "collectedAt": now_str,
            })
            print(f"   [{len(collected)}] {account} · {date}  {title[:36]}")
            if len(collected) >= count:
                break

        begin += 5

    if not collected:
        print(f"\n✗ 未搜到关于「{keyword}」的文章。")
        return 1

    # fetch full content
    if fetch_content and collected:
        print(f"\n   ↓ 抓取正文（{len(collected)} 篇）…")
        for i, rec in enumerate(collected, 1):
            if not rec["link"]:
                continue
            res = fetch_article(rec["link"])
            rec["content"] = res["content"]
            rec["images"] = res["images"]
            if not rec["cover"] and res.get("ogCover"):
                rec["cover"] = res["ogCover"]
            flag = (f"{len(res['content'])} 字 / {len(res['images'])} 图"
                    if res["content"] else "未取到")
            print(f"      {i}/{len(collected)} {flag}")

    # save
    json_path = save_json(collected, [keyword], "")
    xlsx_path = save_excel(collected)

    print("\n" + "=" * 64)
    print(f"✅ 完成：关键词「{keyword}」共 {len(collected)} 篇")
    print(f"   JSON ：{json_path}")
    if xlsx_path:
        print(f"   Excel：{xlsx_path}")
    if auto_ingest:
        ingest_to_kb(collected)
    print("=" * 64)
    return 0


# ════════════════════════════════════════════════════════════════
#  子命令：collect / read / whoami
# ════════════════════════════════════════════════════════════════

def run_collect(accounts, since, count, fetch_content, auto_ingest):
    print("=" * 64)
    print("  墨摘 · 微信公众号采集（Cookie+Token）")
    print("=" * 64)

    token, cookie = load_credentials()
    if not token or not cookie:
        print_credential_guide()
        return 1
    if not accounts:
        print("⚠️  未指定公众号。用法：collect <名称...> 或在脚本顶部配置 ACCOUNTS。")
        return 1

    print(f"配置：{len(accounts)} 个公众号 | 日期≥{since or '不限'} | "
          f"每号目标 {count} 篇 | 抓正文={fetch_content} | 自动入库={auto_ingest}")

    session = make_session(cookie)
    all_articles = []
    interrupted = False

    for name in accounts:
        try:
            all_articles.extend(collect_account(session, token, name, since, count, fetch_content))
        except AuthError as e:
            print(f"\n❌ 凭证问题：{e}")
            print_credential_guide()
            interrupted = True
            break
        except FreqControlError as e:
            print(f"\n⏸  {e}")
            print("   已保存已采集进度，可稍后重跑（建议减小 --count 或增大延时）。")
            interrupted = True
            break
        except ApiError as e:
            print(f"   ⚠️ 跳过「{name}」：{e}")
            continue

    if not all_articles:
        print("\n未采集到任何文章。请检查公众号名称、凭证有效性或日期阈值。")
        return 1

    json_path = save_json(all_articles, accounts, since, partial=interrupted)
    xlsx_path = save_excel(all_articles, partial=interrupted)

    print("\n" + "=" * 64)
    print(f"✅ 完成{'（部分，已中断）' if interrupted else ''}：共 {len(all_articles)} 篇")
    print(f"   JSON ：{json_path}")
    if xlsx_path:
        print(f"   Excel：{xlsx_path}")
    if auto_ingest:
        ingest_to_kb(all_articles)
    print("\n下一步：`python3 kb.py stats` 看进度；`python3 kb.py list --unanalyzed --json`")
    print("       取批次让 agent 逐篇拆解 → `kb.py apply` 写回 → `kb.py export-html`。")
    print("=" * 64)
    return 130 if interrupted else 0


def resolve_sogou_link(url):
    """尽力把搜狗跳转链接解析为 mp.weixin.qq.com 原文（best-effort）。"""
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Referer": "https://weixin.sogou.com/"},
                         timeout=20, allow_redirects=True)
        if "mp.weixin.qq.com" in r.url:
            return r.url
        html = r.text or ""
        parts = re.findall(r"url\s*\+=\s*'([^']*)'", html)
        if parts:
            u = "".join(parts).replace("@", "").replace("&amp;", "&")
            if u.startswith("http"):
                return u
        m = re.search(r"https?://mp\.weixin\.qq\.com/s[^\"'\\<>\s]+", html)
        if m:
            return m.group(0).replace("&amp;", "&")
    except requests.RequestException:
        pass
    return url


def run_read(url, out=None):
    """单篇全文抓取（粘链场景），输出 JSON（形态对齐 web /api/article）。"""
    if not re.match(r"^https?://", url):
        print("✗ url 格式不正确（应以 http(s):// 开头）。")
        return 1
    if "weixin.sogou.com/link" in url:
        url = resolve_sogou_link(url)

    headers = {"User-Agent": UA, "Accept": "text/html,*/*", "Referer": MP_BASE + "/"}
    try:
        r = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        print(f"✗ 抓取失败：{e}")
        return 1
    html = r.text or ""
    final_url = r.url or url
    if is_blocked_page(html):
        print("✗ 目标页触发验证（链接可能过期或被风控）。请改用浏览器打开后粘贴正文。")
        return 1

    title = (pick(html, r'<h1[^>]*class="rich_media_title"[^>]*>([\s\S]*?)</h1>')
             or pick(html, r'<meta property="og:title" content="([^"]*)"')
             or pick(html, r'<title>([\s\S]*?)</title>'))
    account = (pick(html, r'<a[^>]*id="js_name"[^>]*>([\s\S]*?)</a>')
               or pick(html, r'var nickname\s*=\s*"([^"]*)"')
               or pick(html, r'<meta property="og:article:author" content="([^"]*)"'))
    publish = (pick(html, r'<em[^>]*id="publish_time"[^>]*>([\s\S]*?)</em>')
               or pick(html, r'var ct\s*=\s*"(\d+)"'))
    if re.fullmatch(r"\d{10}", publish or ""):
        publish = unix_to_date(publish)
    content_html = slice_content_html(html)
    content = html_to_text(content_html) if content_html else ""
    images = extract_images(content_html)
    cover = pick(html, r'<meta property="og:image" content="([^"]*)"')

    if not content or len(content) < 20:
        print("✗ 未解析到正文（可能是图文/视频类推送或页面结构特殊）。请粘贴正文。")
        return 1

    rec = {
        "id": canonical_id(final_url, title),
        "account": account, "query": "", "title": title,
        "digest": pick(html, r'<meta property="og:description" content="([^"]*)"'),
        "link": final_url, "cover": cover, "images": images,
        "publishDate": publish, "content": content,
        "collectedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        print(f"✓ 已写入 {out}（{len(content)} 字 / {len(images)} 图）。")
    else:
        print(json.dumps(rec, ensure_ascii=False, indent=2))
    return 0


def run_whoami():
    """用一次轻量 searchbiz 校验 token/cookie 是否有效。"""
    token, cookie = load_credentials()
    if not token or not cookie:
        print("✗ 未检测到 token/cookie。")
        print_credential_guide()
        return 1
    print("· 正在用一次轻量搜索校验登录态…")
    session = make_session(cookie)
    try:
        api_get(session, token, "/cgi-bin/searchbiz",
                {"action": "search_biz", "begin": "0", "count": "1", "query": "微信"})
        print(f"✓ 登录态有效。token=…{token[-4:]}，cookie 长度={len(cookie)}。可以开始采集。")
        return 0
    except AuthError as e:
        print(f"✗ 凭证失效：{e}")
        print_credential_guide()
        return 1
    except FreqControlError as e:
        print(f"⏸ 命中频控（但凭证看起来有效）：{e}")
        return 0
    except ApiError as e:
        print(f"⚠️ 接口异常：{e}")
        return 1


# ════════════════════════════════════════════════════════════════
#  入口
# ════════════════════════════════════════════════════════════════

def build_parser():
    p = argparse.ArgumentParser(
        prog="wechat_collector.py",
        description="墨摘 · 微信公众号采集（cookie+token 后台接口，P0 全文挖掘）")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("collect", help="按公众号名称采集文章（默认抓正文 + 自动入库）")
    sp.add_argument("accounts", nargs="+", help="公众号名称（可多个，精确名命中率更高）")
    sp.add_argument("--since", default=DATE_FILTER_THRESHOLD, help="仅采此日期(含)之后；传空串则不限")
    sp.add_argument("--count", type=int, default=TARGET_COUNT, help="每号目标篇数")
    sp.add_argument("--no-content", action="store_true", help="只采列表元数据，不抓正文")
    sp.add_argument("--no-kb", action="store_true", help="不自动入知识库")

    sp = sub.add_parser("read", help="抓取单篇文章全文（粘链场景）")
    sp.add_argument("url")
    sp.add_argument("--out", help="写入 JSON 文件路径（默认打印到 stdout）")

    sub.add_parser("whoami", help="校验 token/cookie 是否有效")

    sp = sub.add_parser("search", help="按关键词搜索全网公众号文章（跨号采集）")
    sp.add_argument("keyword", help="搜索关键词")
    sp.add_argument("--count", type=int, default=10, help="目标篇数")
    sp.add_argument("--no-content", action="store_true", help="只采列表元数据，不抓正文")
    sp.add_argument("--no-kb", action="store_true", help="不自动入知识库")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.cmd == "collect":
        return run_collect(args.accounts, args.since.strip(), args.count,
                           not args.no_content, not args.no_kb)
    if args.cmd == "read":
        return run_read(args.url, args.out)
    if args.cmd == "whoami":
        return run_whoami()
    if args.cmd == "search":
        return run_search(args.keyword, args.count, not args.no_content, not args.no_kb)

    # 无子命令 → 旧用法：读文件顶部默认配置
    return run_collect(ACCOUNTS, DATE_FILTER_THRESHOLD, TARGET_COUNT, FETCH_CONTENT, AUTO_INGEST)


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        print("\n已手动中断。")
        sys.exit(130)

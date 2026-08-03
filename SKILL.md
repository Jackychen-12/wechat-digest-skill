---
name: wechat-digest
description: 微信公众号情报采集 + 知识库 + 结构性分析助手。以「微信公众平台后台」的 Cookie+Token 接口为核心（searchbiz+appmsg），按公众号名称稳定拉取文章列表与全文（注：search 关键词搜索已验证不可用，search_article 返回 ret=200009 not found），落盘 JSON + Excel 并持续累积进本地知识库（knowledge_base.json）；采集内置频控退避、风控验证页识别与「中断也保住进度」；由本地 agent 做逐篇五段式精析（一句话总结/核心观点/关键数据/关键词标签/适用人群）+ 知识库结构化（主题聚类/标签倒排/交叉引用）+ 收藏夹拆解；再生成一个自包含、可双击打开的离线 HTML 工作台（总览/文章库/知识库/收藏夹），并预留 /api/chat 接口做按需 AI 拆解。当用户需要：(1) 抓取/采集某公众号的历史文章全文；(2) 监测竞品或标杆公众号内容动向；(3) 把一批公众号文章建成知识库并做结构化分析/收藏夹拆解；(4) 把分析结果做成离线 HTML 呈现；(5) 解决公众号信息爬取/反爬/频控/采集空结果问题时使用。触发词：公众号抓取、公众号采集、公众号爬取、公众号全文、公众号文章分析、公众号知识库、收藏夹拆解、重点内容解析报告、离线 HTML 报告、wechat digest。
version: 0.4.0
name_zh: 微信公众号采集 · 知识库 · 离线 HTML 工作台
---

# 微信公众号采集 · 知识库 · 离线 HTML 工作台

对标 redbook（rednote）skill，但面向**微信公众号**。三件事，职责分明：

1. **采集（P0，脚本）**：以微信公众平台后台 `searchbiz`+`appmsg` 接口，按名称稳定拉**全文**。
   （`search` 关键词搜索已经真实凭证验证为**不可用**：`search_article` 返回 `ret=200009 not found`。）
2. **知识库（脚本 + 本地 agent）**：把每次采集**去重合并**进一个持续累积的 `knowledge_base.json`；
   由本地 agent 做逐篇五段式精析 + 主题/标签/交叉引用结构化（**不调外部大模型 API**）。
3. **呈现（脚本 + 浏览器）**：生成**自包含离线 HTML 工作台**，可建**收藏夹**、看**知识库**、读**逐篇/收藏夹拆解**；
   并**预留 `/api/chat` 接口**，填了 Key 就能在页面里按需做即时 AI 拆解。

> 公众号没有公开官方 API、浏览器直连强反爬。后台 Cookie+Token 是目前**最稳**的全文采集路径。

---

## 文件结构

```
wechat-digest-skill/
├── SKILL.md                    # 本文档（技能说明）
├── README.md                   # 30 秒上手
├── requirements.txt            # requests + openpyxl（仅采集/Excel 需要）
├── credentials.example.json    # 凭证模板 → 复制为 credentials.json 填写
├── wechat_collector.py         # ① 采集 CLI：collect / search / read / whoami
├── kb.py                       # ② 知识库 CLI：ingest/stats/list/apply/... （纯 stdlib）
├── render_html.py              # ③ 离线 HTML 生成器（纯 stdlib）
├── assets/digest_template.html # 离线工作台模板（内嵌 CSS/JS）
└── output/                     # 产物（已 .gitignore）
    ├── articles_YYYYMMDD.json      # 原始采集（接口契约，可被 Web 应用「导入 JSON」）
    ├── index_YYYYMMDD.xlsx         # Excel 索引
    ├── knowledge_base.json         # 持续累积知识库（单文件，去重合并）
    └── digest.html                 # 离线自包含工作台（双击打开）
```

依赖底线：`kb.py` / `render_html.py` **仅用标准库**，无网/无 pip 也能跑；只有 `wechat_collector.py`
需要 `requests`，Excel 需要 `openpyxl`（缺失则自动跳过 Excel）。

---

## 前置准备

### 1. 安装依赖

```bash
cd skill
pip install -r requirements.txt      # requests + openpyxl
```

### 2. 获取 token + cookie（关键一步）

> 需要一个微信公众平台账号。**个人订阅号免费**即可，不必是企业号。

1. 浏览器登录 <https://mp.weixin.qq.com>
2. 看地址栏 URL，形如 `.../cgi-bin/home?...&token=1234567890` —— 末尾 `token=` 后的那串**数字**就是 token。
3. `F12` → **Network** → 刷新 → 点任意 `mp.weixin.qq.com` 请求 → **Request Headers** 里复制**整条** `Cookie`。

```bash
cp credentials.example.json credentials.json   # 编辑填入 token 和 cookie
python3 wechat_collector.py whoami             # 校验登录态是否有效
```

（也可改用环境变量 `WECHAT_TOKEN` / `WECHAT_COOKIE`。）

> ⚠️ token/cookie 通常几小时~几天就过期。采集失败优先重新获取。`credentials.json` 已 `.gitignore`，勿提交。

---

## ① 采集（P0 · 全文挖掘）—— `wechat_collector.py`

> **采集路径优先级**：`collect`（appmsg 后台接口）是**默认首选**——最直接、最准确，
> 按公众号真实文章列表拉全文。`collect-urls`（URL/RSS）是**备选**，仅当 `collect` 被频控
> 持续拿不到、且**经用户确认**时才用（它的完整性只取决于所给来源，不如 appmsg 权威）。

```bash
# 按名称采集（默认抓正文 + 自动入知识库）
python3 wechat_collector.py collect 晚点LatePost --since 2025-01-01 --count 10
python3 wechat_collector.py collect 晚点LatePost 虎嗅APP --count 5      # 多个号
python3 wechat_collector.py collect 某号 --no-content                   # 只采列表元数据（更快、更不易频控）
python3 wechat_collector.py collect 某号 --no-kb                        # 只落盘，不自动入库

# 单篇全文抓取（粘链场景，输出 JSON；支持 mp 链接，尽力解析搜狗跳转链接）
python3 wechat_collector.py read "https://mp.weixin.qq.com/s/xxxx" --out output/one.json

# ★ 备选采集（appmsg 被频控锁死时用）：从 URL / 文件 / RSS 批量拓全文，不经 appmsg
python3 wechat_collector.py collect-urls urls.txt                       # 文件：每行一个 mp 链接（# 注释）
python3 wechat_collector.py collect-urls "https://mp.weixin.qq.com/s/aaa" "https://mp.weixin.qq.com/s/bbb"
python3 wechat_collector.py collect-urls https://rsshub.app/wechat/xxx  # RSS/Atom 订阅源→自动抽 mp 链接
python3 wechat_collector.py collect-urls articles.json --no-kb         # 也吃既有 articles_*.json

# 校验凭证
python3 wechat_collector.py whoami

# 按关键词搜索全网公众号文章（跨号采集）
python3 wechat_collector.py search "AI Agent" --count 10
python3 wechat_collector.py search "大模型落地" --count 20 --no-content   # 只采列表
python3 wechat_collector.py search "具身智能" --no-kb                     # 不自动入库

# 无参数运行 → 读文件顶部默认配置 ACCOUNTS / DATE_FILTER_THRESHOLD ...（旧用法，向后兼容）
python3 wechat_collector.py
```

**`collect` 选项**：`--since YYYY-MM-DD`（仅采此日期后；传 `--since ""` 不限）、`--count N`（每号目标篇数）、
`--no-content`、`--no-kb`、`--freq-cooldown`、`--freq-retries`。

**`collect-urls`（备选采集，仅在 appmsg 被锁且经确认后用）**：`sources`（一或多个，可混合）+ `--no-kb`。
它走单篇公开页（与 appmsg **两套限流**），所以 `collect`（appmsg）被 `ret=200013` 锁住的当天依然能拿到全文。
sources 可以是：mp 文章链接、本地文件（`.txt` 每行一个 / `.json` 数组或 `articles_*.json` / `.opml`）、
或 RSS/Atom 订阅链接（自动抽出其中的 mp 链接）。产物与 `collect` 一致（`articles_*.json` + Excel + 自动入库）。
> ⚠ **它是备选、不是首选**：`collect`（appmsg）按公众号真实历史拉，最完整最准确；而 `collect-urls`
> 的完整性只取决于你能凑到的 URL（可能漏文章，甚至串到同名号）。所以它**不会被自动触发**——
> 仅当 `collect` 持续被频控拿不到、且由你确认并提供来源时才走它。

**`search` 选项**：`keyword`（位置参数，搜索关键词）、`--count N`（目标篇数，默认 10）、
`--no-content`（只采列表元数据）、`--no-kb`（不自动入知识库）。

> 🚫 **`search` 已经真实凭证验证为不可用，不要再用。** 它调的
> `operate_appmsg?action=search_article` 在登录态有效时返回 **`ret=200009, err_msg="not found"`**
> ——即该 action 并不存在。微信公众平台后台**未提供「全网文章搜索」能力**，
> 所以这个子命令**永远返回空**，不是频控也不是网络问题，重试无意义。
> 命令仍保留（向后兼容）但会在开头直接警告。**请一律改用 `collect <公众号名>`。**

**产物**：`output/articles_YYYYMMDD.json`（含正文，**供分析**）+ `index_YYYYMMDD.xlsx`（索引表）。
采集成功后默认自动 `kb ingest` 入库。中断（频控/凭证失效）时生成 `*_partial.json/.xlsx` 保留进度。

**抓取加固**：
- 随机延时 + 指数退避；频控（`ret=200013`）自动冷却重试，默认首次 **300s 且逐次翻倍**
  （微信实际冷却是几十分钟级，可用 `--freq-cooldown` / `--freq-retries` 自调）。
- **中断一定保住进度**：已采到的文章边采边落入结果集，频控/凭证失效时先把这批正文补完
  再退出并写 `*_partial` 文件（退出码 130）。重跑按 `sn=` 去重，不会重复入库。
- 正文取不到时退回 `og:description`（会标记为 `digest` 状态）；记录封面 `cover` 与正文内**图片 URL 列表** `images`。
- **空因可观测**：每批抓完会汇报「正文成功 N/M、空率 X%」与空因分布（见下表）。

---

## ② 知识库（持续累积 + 结构性分析）—— `kb.py` + 你（本地 agent）

知识库是单文件 `output/knowledge_base.json`，按规范化 `id`（优先链接里的 `sn=`，否则散列）**去重合并**，
跨多次采集**持续累积**。结构：

```jsonc
{
  "version": 1, "updatedAt": "...",
  "topics":      [{"id","name","keywords":[],"articleIds":[]}],   // 主题聚类（agent 维护）
  "tags":        {"标签": ["articleId", ...]},                     // 标签→文章倒排（由 analysis.tags 自动派生）
  "collections": {"收藏夹名": {"name","articleIds":[],"breakdown":{...}}},  // 收藏夹（页面建/导入）
  "articles":    {"<id>": {
     "id","account","title","link","cover","images":[],"publishDate","digest","content","collectedAt",
     "analysis": {"summary","viewpoints":[],"data":[],"tags":[],"audience"},  // 五段式（agent 写回）
     "topicIds":[], "crossRefs":[]                                            // 结构（agent 写回）
  }}
}
```

### kb.py 命令

```bash
python3 kb.py ingest output/articles_20250624.json   # 采集结果入库（采集时已自动做，可手动补）
python3 kb.py stats                                   # 进度：篇数/已分析比例/主题/标签/收藏夹
python3 kb.py list --unanalyzed --content --json      # 取「待分析」批次（含正文）给 agent
python3 kb.py get <id>                                 # 打印单篇完整 JSON
python3 kb.py apply --file batch.json                 # ★ agent 一次写回一批分析+结构（主路径）
python3 kb.py set-analysis <id> --file a.json         # 写回单篇五段式（备用）
python3 kb.py set-meta <id> --topics ai --tags 大模型,推理 --crossrefs <id2>   # 写回单篇结构（备用）
python3 kb.py topic-upsert ai --name "AI 与大模型" --keywords 大模型,推理       # 建/改主题
python3 kb.py import-collections output/collections.json   # 导入离线页导出的收藏夹
python3 kb.py export-html                              # 生成离线 HTML 工作台（= render_html.py）
python3 kb.py rebuild                                  # 重建索引（一般无需手动）
```

### 知识库分析工作流（你 = 本地 agent 执行，**不调外部模型 API**）

1. **取批次**：`python3 kb.py list --unanalyzed --content --json`，逐篇取 `id/title/account/content`。
   正文为空的篇目以 `digest` 为准并在 `summary` 里标注「正文未取到」。

2. **逐篇五段式精析**：每篇产出固定五段（信息密度高、不复述原文、关键数据带数字、标签 3~6 个）：
   `summary`（一句话总结）、`viewpoints[]`（核心观点）、`data[]`（关键数据/事实）、
   `tags[]`（关键词标签）、`audience`（价值与适用人群）。

3. **知识库结构化（架构在此成形）**：
   - **主题聚类**：先 `topic-upsert` 建若干主题（如 `ai` / `biz`），再给每篇指派 `topicIds`。
   - **标签倒排**：标签写在 `analysis.tags`，`tags` 倒排索引由 kb 自动派生。
   - **交叉引用**：把同主题/有承接关系的文章互相写进 `crossRefs`（双向更佳）。

4. **批量写回**：把以上整理成一个 `batch.json` 一次 `kb.py apply`：

   ```jsonc
   {
     "topics": [{"id":"ai","name":"AI 与大模型","keywords":["大模型","推理"]}],
     "articles": {
       "<id>": {
         "analysis": {"summary":"…","viewpoints":["…"],"data":["…"],"tags":["大模型","推理"],"audience":"…"},
         "topicIds": ["ai"], "crossRefs": ["<otherId>"]
       }
     }
   }
   ```

5. **校验**：`python3 kb.py stats` 看「已分析比例」升高、主题/标签分布合理。

---

## ③ 离线 HTML 工作台 —— `render_html.py`

```bash
python3 kb.py export-html        # 或：python3 render_html.py → 生成 output/digest.html
# 两种看法：
#   A) 双击 output/digest.html（file://）——零依赖、无需联网，但收藏夹只存浏览器、清缓存会丢
#   B) python3 kb.py serve        ——推荐：起本地服务，建/改收藏夹会自动存回 knowledge_base.json
#      再浏览器打开 http://127.0.0.1:8765/digest.html（清缓存 / 换浏览器都不丢）
```

`digest.html` 是**自包含单文件**（数据/样式/脚本全内嵌，沿用「墨摘」editorial 设计）。四个视图：

- **总览**：篇数 / 已拆解占比 / 公众号·主题·标签分布 / 时间范围。
- **文章库**：按公众号/主题/标签/关键词筛选；每篇点开看五段式拆解；**⭐ 加入收藏夹**。
- **知识脉络**：关系图谱（文章成节点、按主题着色、`crossRefs` 连线、点节点聚焦邻域、拖拽/缩放/图例筛选）+ 词条详情（一句话总结、所属主题、关键词、**本文引用** 与 **被谁引用/反向链接**、在文章库中打开）+ 脉络索引（主题聚类 / 标签云 / 发布时间线）。
- **收藏夹**：建夹 / 看**收藏夹拆解**（agent 写回的 breakdown + 页面即时聚合）/ 建议阅读顺序 / 导出 JSON。
  用 `kb.py serve` 打开时，建夹/增删会**自动存回 `knowledge_base.json`**（页头显示「收藏夹自动存盘 ✓」）。

### 收藏夹持久化

- **推荐：`python3 kb.py serve` 打开** → 收藏夹自动存回知识库（清缓存、换浏览器、换设备都不丢）。
- **双击 file:// 打开** → 收藏夹存浏览器 `localStorage`（清「站点数据」会丢），此时靠下面的导出/导入闭环兜底。

### 收藏夹闭环（导出 / 导入，file:// 下兜底）

1. 在「文章库」用 ⭐ 把文章加进命名收藏夹。
2. 「收藏夹」页点「**导出收藏夹 JSON**」下载 `collections.json`。
3. `python3 kb.py import-collections output/collections.json` 导入库。
4. 你（agent）对该收藏夹做**深度拆解**，写进对应 `collections[名].breakdown`（用 `kb.py apply` 的
   `"collections"` 字段，breakdown 可用结构化对象：`themes/methods/keyData/insights/readingOrder`，
   或直接 `{"markdown":"..."}`）。
5. 重新 `kb.py export-html` → 页面「收藏夹拆解」即出现深度分析。

### 预留 AI 接口（可选）

页面右上 ⚙︎「设置」可填 后端 URL（默认现有 Deno 后端）+ provider/model + 本地 API Key，按现有
`/api/chat` 流式协议做**按需即时拆解**（逐篇 / 整收藏夹）。**Key 只存浏览器 `localStorage`，绝不写入
文件或知识库**。不填则页面只读 agent 预生成的分析——这与「采集用脚本、分析用本地 agent」的默认路径一致。

---

## 数据字段说明

`articles_*.json` 与知识库 `articles[*]` 中每篇：

| 字段 | 说明 |
|------|------|
| `id` | 规范化主键（链接里的 `sn=`，或散列）——去重合并用 |
| `account` / `query` | 公众号昵称 / 采集检索名 |
| `title` / `digest` | 标题 / 摘要 |
| `link` / `cover` / `images` | 文章 URL / 封面 / 正文内图片 URL 列表 |
| `publishDate` / `content` | 发布日期 / 清洗后正文纯文本 |
| `collectedAt` | 采集时间戳 |
| `analysis` / `topicIds` / `crossRefs` | agent 写回的五段式分析 / 主题 / 交叉引用 |
| `contentStatus` | 正文抓取结果状态（见下表）——只在 `articles_*.json`/Excel 里，不入知识库 |

### `contentStatus`：「空」到底是为什么

以前风控页、失效链接、已删文章、真的没正文——四种情况都被压成同一个空串，
“空了但没人知道为什么”。现在逐篇标出原因：

| 状态 | 含义 | 能不能重试 |
|------|------|-----------|
| `ok` | 正常取到正文 | — |
| `digest` | 正文未取到，已用 `og:description` 兜底 | 可重试 |
| `empty` | 页面结构正常但确实无正文（纯图片/视频推送） | 重试无用 |
| `share` | 「分享一篇文章」卡片页，未能跟到原文 | 可重试 |
| `blocked` | **被微信验证页拦截** | 降速/换 IP 后重采 |
| `gone` | 已删除 / 违规下架 | 不可恢复 |
| `invalid` | 链接失效或参数错误 | 需重新获取链接 |

> 关于 `blocked`：微信真正的反爬页是 301/302 重定向到
> `/mp/wappoc_appmsgcaptcha?poc_token=...`，它的 `<title>` 是空的、正文区没有任何中文提示词，
> **只看 HTML 内容根本识别不了**，必须看最终 URL。这是以前大量「静默空正文」的根因。
> 命中 `blocked` 时请隔一段时间对同一批重跑，`kb ingest` 会自动用更长的正文补全。

> **互动量说明**：阅读数 / 在看 / 点赞需要微信客户端的签名参数，后台接口**拿不到**，故**不包含**。
> 本技能提供标题/摘要/正文/时间/图片等**内容数据**。如需互动量，需走移动端抓包方案（不在本技能范围）。

---

## 常见问题与排错

- **未检测到有效 token/cookie**：确认已填 `credentials.json`（非占位符）；token 是 URL 里 `token=` 后纯数字，cookie 是 Request Headers 里**整条**。先跑 `whoami`。
- **凭证失效 / 接口未返回 JSON（ret=200003）**：token/cookie 过期了（很常见）。重新登录获取。注意：**此时已采到的文章依旧会写入 `*_partial` 文件**，不会白跑。
- **命中频控（ret=200013）**：脚本会自动冷却重试（默认 300s 逐次翻倍）；仍失败会**把已采进度落盘后**退出（退出码 130）。微信实际冷却常在几十分钟以上，别暗重试；缓解：减小 `--count`、减少账号数、调大 `--freq-cooldown`。
- **搜不到 / 选错号**：用更精确全称；脚本对多结果会打印候选与已选（含 `fakeid`）。
- **部分文章「正文未取到」**：看每批末尾汇报的**空因分布**对照上表。`empty`/`gone` 属正常（分析时以 `digest` 为准并标注）；若大量 `blocked` 则是被风控，**应降速后重跑而不是当作没正文**。
- **`search` 总是空**：这不是 bug 也不是频控——已用真实凭证验证 `search_article` 返回 `ret=200009 not found`，该接口不存在，微信后台没有全网文章搜索能力。**请改用 `collect <公众号名>`。**
- **`searchbiz` 能搜到号、但 `appmsg` 一上来就 200013**：这是最常见的「用不了」形态。两个接口的频控额度是分开的：搜公众号很宽松，而「拉别人的文章列表」卡得很紧。一旦命中只能等（常见几十分钟到数小时，甚至当日额度用完），换 IP 或换 token 都不一定管用。建议：小 `--count`、一次只采 1 个号、两次采集之间隔开一段时间。
- **搜狗链接解析失败**：若提示搜狗 `/antispider`，是搜狗对当前 IP 启用了验证，换用 mp 原文链接或稍后再试。
- **digest.html 打开是空的**：先 `kb.py stats` 确认库里有文章；再 `kb.py export-html` 重新生成。断网时字体降级为系统字体，数据照常显示。

---

## 安全与合规

- **凭证仅本地**：`credentials.json` / `cookie.txt` 已 `.gitignore`，严禁提交或外传。
- **API Key 仅本地**：离线页的 Key 只存浏览器 `localStorage`，**绝不**写入文件或知识库。
- **控制频率**：避免短时高频采集；遇频控耐心等待，勿暴力重试。
- **用途限制**：仅供个人学习与研究；遵守目标站点服务条款与 `robots`；勿用于商业用途或对外公开他人内容。

---

## 与「墨摘」Web 应用的关系

本技能是仓库 Web 应用（GitHub Pages 前端 + Deno 后端）之外的**命令行 / agent 工作流补充**：

- Web 应用走**搜狗微信搜索**（免登录但只能拿近期少量文章、易触发验证码）。
- 本技能走**公众号后台 Cookie+Token**（需登录但能稳定拉全量历史全文），分析交给本地 agent，产出离线 HTML 工作台。

两者数据结构互通：本技能的 `articles_*.json` 可被 Web 应用「导入 JSON」直接加载（`link→url`、`digest→summary`）；
离线工作台也复用了 Web 应用同款 `/api/chat` 流式协议作为可选 AI 接口。

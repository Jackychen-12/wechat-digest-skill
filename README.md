<div align="center">

# 微信公众号采集 · 知识库 · 离线 HTML 工作台

**一个 Claude 技能（Agent Skill）：按名称稳定拉公众号全文 → 攒成本地知识库 → 生成可双击打开的离线 HTML 工作台**

<sub>纯本地运行 · 凭证只存本地 · 产物自包含 · 断网也能看</sub>

[![Skill](https://img.shields.io/badge/Claude-Agent_Skill-d8401f?style=for-the-badge)](#-安装作为-claude-技能)
[![Python](https://img.shields.io/badge/Python-3.8+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

</div>

---

<p align="center">
  <img src="docs/xhs/page-01.png" width="40%" alt="给 Claude 装个公众号采集技能" />
  &nbsp;
  <img src="docs/xhs/page-04.png" width="40%" alt="离线 HTML 工作台" />
</p>

---

## ✨ 这是什么

把「追公众号太累」这件事拆成三步，职责分明：

| 步骤 | 谁来做 | 产物 |
| --- | --- | --- |
| **① 采集** | `wechat_collector.py`（脚本） | 按公众号名称稳定拉**全文**，或按关键词跨号搜全网文章 → `articles_*.json` + Excel |
| **② 知识库** | `kb.py` + 本地 agent | 去重合并进 `knowledge_base.json`，逐篇五段式精析（总结/观点/数据/标签/适用人群）+ 主题聚类/标签倒排/交叉引用 |
| **③ 呈现** | `render_html.py` + 浏览器 | 生成**自包含离线 HTML 工作台**（总览 / 文章库 / 知识库 / 收藏夹），双击即开 |

> 公众号没有公开官方 API、浏览器直连强反爬。本技能以**微信公众平台后台 Cookie+Token** 为核心，是目前最稳的全文采集路径。
> `kb.py` / `render_html.py` **仅用标准库**，无网、无 pip 也能跑；只有采集这步需要 `requests`（Excel 需 `openpyxl`）。

---

## 📦 安装（作为 Claude 技能）

把本仓库 clone 进 Claude 的技能目录，重开会话即可被识别：

```bash
git clone https://github.com/Jackychen-12/wechat-digest-skill.git \
  ~/.claude/skills/wechat-digest-skill
```

之后在 Claude 里直接说：**「采集 晚点LatePost 最近 10 篇」**「把这些文章建成知识库做结构化分析」「生成离线 HTML 工作台」即可触发。

> 也可以完全不依赖 Claude，当成普通命令行工具用（见下方「30 秒上手」）。

---

## 🚀 30 秒上手（命令行）

```bash
cd ~/.claude/skills/wechat-digest-skill
pip install -r requirements.txt          # requests + openpyxl

cp credentials.example.json credentials.json
#  ↑ 登录 https://mp.weixin.qq.com 取 token（地址栏 URL 里的 token=数字）
#    + cookie（F12 → Network → 任一请求的 Request Headers → Cookie 整串），填进去
python3 wechat_collector.py whoami        # 校验登录态

# ① 采集（默认抓正文 + 自动入知识库）
python3 wechat_collector.py collect 晚点LatePost --since 2025-01-01 --count 10

# ② 让本地 agent 拆解：取批次 → 写回（详见 SKILL.md「知识库分析工作流」）
python3 kb.py list --unanalyzed --content --json     # 取待分析批次
python3 kb.py apply --file batch.json                # 写回五段式 + 主题/标签/交叉引用
python3 kb.py stats                                  # 看进度

# ③ 生成离线 HTML 工作台
python3 kb.py export-html        # → output/digest.html（双击打开；收藏夹仅存本浏览器）
python3 kb.py serve              # 推荐：本地服务，收藏夹自动存回 knowledge_base.json
#                                  → 浏览器开 http://127.0.0.1:8765/digest.html
```

完整命令、参数与工作流见 **[SKILL.md](./SKILL.md)**。

---

## 🖥 HTML 展示方式

`kb.py export-html` 会把整个知识库渲染进一个**单文件 `output/digest.html`**（CSS/JS 全部内嵌）：

- 双击就能打开，不需要服务器、不联网；
- 四个视图：**总览 / 文章库 / 知识库 / 收藏夹**；
- 在浏览器里建收藏夹、读逐篇与收藏夹拆解；
- 用 `kb.py serve` 启动时，收藏夹会自动存回 `knowledge_base.json`（清缓存也不丢）。

模板源码在 [`assets/digest_template.html`](./assets/digest_template.html)，可自行改造样式。

---

## 🔑 凭证怎么拿

1. 浏览器登录 [mp.weixin.qq.com](https://mp.weixin.qq.com)；
2. **token**：地址栏 URL 形如 `.../cgi-bin/home?...&token=1234567890`，末尾那串数字；
3. **cookie**：F12 → Network → 刷新 → 点任一 `mp.weixin.qq.com` 请求 → Request Headers 里复制整条 Cookie 的值；
4. 填进 `credentials.json`（已被 `.gitignore`，**绝不会提交**）。

> ⚠️ token / cookie 会过期，采集失败优先重新获取。
> ⚠️ 产物（`articles_*.json` / `knowledge_base.json` / `digest.html` 等）都在 `output/`，已 `.gitignore`。
> ⚠️ 本工具仅供个人学习与研究，请遵守目标站点的 robots 与服务条款，控制访问频率。

---

## 📄 License

MIT

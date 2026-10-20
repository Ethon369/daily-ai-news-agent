# 每日 AI 新闻助手

KUAFUAI Agent 开发实习生选项 A。按订阅偏好整理最近 24 小时的 AI 新闻，保存历史并通过 QQ SMTP 推送。核心是手写 function calling 循环：LLM 决定工具顺序、次数与何时提交结果，程序负责校验、预算及权限边界。

## 快速运行

前提：Python 3.11+、网络、可调用的 DeepSeek API Key。Windows 的 `bash` 工具需要 Git for Windows；Linux/macOS 需要 Bash。已在 Windows Python 3.12.14 环境验证，Docker 镜像以 Python 3.11 为基础。

在项目根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`，填写 `DEEPSEEK_API_KEY`。如系统环境变量已存在，无需重复填写；环境变量优先于 `.env`。模型默认 `deepseek-flash`，可通过 `DEEPSEEK_MODEL` 修改。

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

打开 **http://127.0.0.1:8000**。数据库与表会在启动时通过 `migrations/001_initial.sql` 自动创建。保存一个订阅，点击“生成预览”，下方逐步显示工具事件；预览不会自动发邮件。

后续可用 `pwsh -File scripts/start.ps1 -SkipInstall` 启动。首次也可直接运行 `pwsh -File scripts/start.ps1` 创建环境并安装依赖。

Linux/macOS：

```bash
cp .env.example .env
# 编辑 .env 后
bash scripts/start.sh
```

依赖和凭据准备好后只有一个启动命令；首次安装耗时取决于 Python、PyPI 与网络，不能将本机缓存安装当成全新机器性能。

## 配置邮件

在 QQ 邮箱官方设置中开通 SMTP，并生成授权码。将以下项目放在本地 `.env`，不要填 QQ 登录密码，也不要提交 `.env`：

```dotenv
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USERNAME=你的QQ邮箱地址
SMTP_FROM=同一QQ邮箱地址
SMTP_PASSWORD=邮箱授权码
```

重启服务后可点击“发送到订阅邮箱”。未配置时可以完成简报生成、历史与工具记录，投递会明确显示配置缺失。

`accepted` 表示 SMTP 服务器接受；是否进入收件箱需实际确认。认证错误、明确拒收可以重试；提交时断线或进程中断标记为 `uncertain`，页面禁止盲目重发，请先核查收件箱。

本次交付按用户选择只做邮件模拟验证，未发送真实邮件。

## 自动生成

- 默认北京时间每日 09:00，可在订阅中修改时间或停用。
- 服务每分钟检查到期任务；关闭服务期间不运行。启动时只补跑当天已到期任务。
- 一个订阅每天只创建一个正式任务；失败后也不会自动创建第二个，排查后可手动预览。
- 手动预览与每日正式任务分开。重复点击预览会复用当前排队/运行中的预览。
- 任务创建时冻结订阅与收件人，修改偏好对后续新任务生效。
- 仅支持单实例单 worker。同一数据目录第二个实例会拒绝启动。

## Agent 如何工作

```text
订阅快照、历史摘要、任务时间
               ↓
         LLM 返回工具调用
               ↓
     校验参数、执行、返回真实结果
               ↓
         LLM 继续选择或完成
               ↓
   结构和来源校验 → 保存简报 → 正式任务提交邮件
```

工具包括题目要求的 `list_dir`、`read_file`、`search_content`、`write_file`、`bash`，以及 `fetch_news`、`read_article`。不强制每轮调用所有工具。

默认上限：12 轮模型响应、24 次工具调用、180 秒。最终 JSON 最多两次修复，仍计入总预算。工具错误回传模型；达到上限明确失败并保留轨迹。

`bash` 真实执行受限命令，只支持 `pwd`、`ls`、`cat`、`head`、`tail`、`wc` 的有限参数。禁止组合命令、通配符、重定向、环境变量扩展与越界路径。Windows 自动查找 Git Bash，也可设置 `BASH_PATH=D:/Git/bin/bash.exe`。这是受限命令工具，不是通用代码沙箱。

## 新闻与数据

来源为 Hugging Face Blog RSS 和 Hacker News 官方 Firebase API 的前 30 个热门条目，按来源时间筛选最近 24 小时。HN 的时间是帖子提交时间，不保证等于原始文章发布时间。未知来源时间明确显示未知。

URL 去重由程序处理，事件合并与个性化筛选由模型处理。最多 5 条，全部来源失败不能生成“没有新闻”；正常空结果允许生成空简报。所有链接由程序根据已采集编号附加，模型不能捏造 URL。

SQLite 文件位于 `data/news.db`；每次运行的材料、草稿和 Markdown 位于 `data/runs/<run_id>/`。模型工作目录不含收件人和凭据；本地数据库含订阅邮箱，应与其他个人数据一样保管。`data/` 和 `.env` 已加入 `.gitignore`。

部分网络代理将域名解析为 `198.18.0.0/15` 的 Fake-IP。遇到仅包含该地址段的 DNS 结果时，应用向固定的 Cloudflare HTTPS DNS 服务查询真实地址，重新校验为公网地址后固定连接，并保留原域名的 HTTPS 证书校验。每次重定向重新校验；本机、内网、混合私网解析与非 HTTP 协议仍被拒绝。公共 DNS 失败时不会连接 Fake-IP 占位地址。此过程不修改系统 DNS 或代理。

网页优先提取 article/main，去除导航、脚本和侧栏，最多返回 12,000 字符；验证页、过短页面及 PDF 不作为正文。页面明确区分原网页正文片段、来源摘要和仅标题。论文摘要页不等同于完整论文，读取片段也不代表阅读全文。旧简报保持生成时的证据标注，重新生成预览才能使用新抓取结果。

新闻筛选和摘要仍可能出错；原始链接、证据范围和工具事件用于复核，不将摘要视为已独立核实的事实。

## API 与测试

REST 接口见 [技术设计](docs/TECHNICAL_DESIGN.md)，交互文档位于 `/docs`。写请求必须带 `X-Requested-With: DailyBrief`，通过页面使用时自动添加。默认仅允许本机 Host 与同源写请求，没有公网账号认证。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
node --check static/app.js
```

Node 只用于可选的 JavaScript 语法检查，应用运行不依赖 Node。

真实外部验证（`--model` 会消耗 API 额度，不发邮件）：

```powershell
.\.venv\Scripts\python.exe scripts/verify_live.py --model
.\.venv\Scripts\python.exe scripts/verify_live.py --model --partial
# 原文修复验证，保留此前验证目录
.\.venv\Scripts\python.exe scripts/verify_live.py --model --output-dir artifacts/article-fix-agent
.\.venv\Scripts\python.exe scripts/verify_articles.py --url https://arxiv.org/abs/2610.18861
# 或独立生成一份不发送邮件的预览
.\.venv\Scripts\python.exe -m app --topics "AI,大语言模型" --keywords "Agent,开源"
```

`--partial` 人为让 Hugging Face 适配器失败，其他新闻源和模型保持真实；这项故障注入不能称作所有来源均为自然故障。结果写入被 Git 忽略的 `artifacts/`。测试夹具仅用于自动测试，生产入口无假新闻/假模型回退。

## Docker

安装 Docker 后，准备 `.env` 并运行：

```bash
docker compose up --build -d
docker compose logs -f app
docker compose down
```

端口仅映射到本机，数据保存在命名卷 `brief_data`。不要使用 `docker compose down -v`，除非确实准备删除这些持久数据。当前环境没有 Docker，因此提供构建配置但未验证容器启动。

## 文档与交付

- [PRD](docs/PRD.md)
- [技术设计](docs/TECHNICAL_DESIGN.md)
- [实施与验收清单](docs/IMPLEMENTATION.md)
- [验证报告](docs/VERIFICATION.md)
- [开源参考](docs/OPEN_SOURCE_REFERENCES.md)
- [三分钟演示流程](docs/DEMO.md)

参考 Horizon 的新闻适配、去重和邮件组织思路，本项目独立实现，无 Horizon 运行依赖。未复制其源码，详细参考范围见说明。

提交代码包时排除 `.env`、`.venv`、`data`、`artifacts`、Python/测试缓存，并按题目要求保留 `.git`。仓库以初始提交记录本次实际完成的实现和验证，不补造此前的阶段历史；后续修改继续保留真实提交记录。

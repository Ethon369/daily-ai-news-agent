# 每日 AI 新闻助手技术设计

## 架构
FastAPI 提供同源静态页面和 REST API。SQLite 保存 subscriptions、runs、events、briefings、deliveries。每次 Agent 运行拥有独立文件目录。后台单消费者处理任务，定时器每分钟插入到期任务。网络调用和子进程不占用数据库事务。

## Agent
手写 function calling 循环：任务与工具定义 → 模型选择 → 参数验证 → 执行 → 工具结果回传 → 下一轮。默认 12 轮、24 次工具、180 秒；两次最终结构修复计入预算。工具错误可由模型调整，达到上限则失败。模型提交 JSON 后由程序核验引用并渲染，不固定工具顺序或强制使用全部工具。

工具：list_dir、read_file、search_content、write_file、bash、fetch_news、read_article。订阅快照不含邮箱；模型只看到话题、关键词与历史摘要。来源适配器为 Hugging Face RSS 与 Hacker News API。采集结果、失败状态、正文获取结果均真实返回。原网页按 article/main 优先提取，过滤验证页和过短内容，处理来源字符集，限制 12,000 字符；证据标签区分正文片段、来源摘要和标题。URL 规范化去掉跟踪参数，程序限制时间窗口及来源引用。

## 数据与接口
所有 API 成功返回 {data: ...}，错误返回 {error: {code, message}}。
- POST/GET /api/subscriptions；PATCH /api/subscriptions/{id}
- POST /api/subscriptions/{id}/runs：202 创建预览
- GET /api/runs/{id}：状态、事件、关联简报
- GET /api/subscriptions/{id}/briefings；GET /api/briefings/{id}
- POST /api/briefings/{id}/deliveries：202；发送中或已接受返回现有记录
- GET /api/health：配置是否存在、数据库状态，不返回凭据

SQLite 开启 WAL、外键与 busy_timeout。正式任务使用部分唯一索引；任务创建时冻结订阅与收件人。启动时将遗留 running 标为 interrupted，遗留 sending 标为 uncertain；排队任务仍可执行。

## 安全边界
本地管理应用，无公开认证；仅监听 127.0.0.1，校验 Host/Origin 和写入请求头，防止网页跨站操作本机服务。工具路径解析后必须位于当前运行目录，禁止符号链接与 Windows 特殊路径。Bash 仅允许 pwd/ls/cat/head/tail/wc 的固定参数语法，禁用组合命令，使用最小环境、超时和有限输出；不是任意代码沙箱。
任意文章链接只允许公网 HTTP/HTTPS，解析 DNS 后固定连接目标，逐跳检查重定向；不允许本机、内网和其他协议。遇到纯 198.18/15 Fake-IP 解析时，向固定 Cloudflare HTTPS DNS 服务查询真实地址，再验证全部地址均为公网并固定连接，保留 HTTPS 主机名和证书校验；混合私网解析拒绝，公共 DNS 失败不回退到 Fake-IP。每个重定向目标都重新执行此流程。网页和邮件文本转义，日志不记录密钥、邮箱、原始推理。文件写入仅限 drafts/，防止覆盖系统准备的证据。模型最终引用取自内存中的采集索引，不能通过改文件伪造来源。

## 邮件与失败
SMTP_SSL 默认 smtp.qq.com:465。收件人来自冻结快照。先保存简报再创建投递；唯一 brief_id 防止并发重复发送。认证失败和明确拒收可重试；DATA 阶段网络断开或进程中断标 uncertain，不自动重发。SMTP 成功提交标 accepted，不声称已进入收件箱。

## 部署
Python >=3.11，本地 Git Bash 或 Linux Bash。Docker 使用 Python 3.11、非 root 用户及持久数据卷。只运行一个 Uvicorn worker；通过进程锁阻止同一数据库被多个实例同时管理。无公网部署支持。

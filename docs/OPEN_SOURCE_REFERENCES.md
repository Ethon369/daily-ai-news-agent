# 开源参考说明

参考项目：https://github.com/Thysrael/Horizon
审阅日期：2026-10-18；交付复核日期：2026-10-19。参考视图为 main；复核时查询到主分支版本 `0eda588fb0486a4b183bb3c613d6f20548514505`，用于后续定位参考项目。本项目没有将它作为固定运行依赖。

参考范围为来源适配器、统一新闻字段、URL 去重、邮件内容组织的设计思路：
- https://github.com/Thysrael/Horizon/blob/main/src/orchestrator.py
- https://github.com/Thysrael/Horizon/blob/main/src/services/email.py
- https://github.com/Thysrael/Horizon/blob/main/src/mcp/README.md

本项目独立实现 Agent 循环、API、存储、界面与工具，无 Horizon 运行依赖，没有复制其源码。Horizon 主编排由程序安排处理阶段，本项目由 LLM 选择工具顺序与停止时机。若后续引入其代码，必须补充准确版本、修改范围及 MIT 版权许可。

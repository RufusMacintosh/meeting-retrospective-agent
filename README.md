# 会议复盘工作台 · Meeting Retrospective Agent

把会议逐字稿整理成带原文出处的决策、风险和行动项，并按项目跟踪后续进展。

**[在线体验](https://meeting-retrospective-studio.a19231843107.chatgpt.site/)** · [示例逐字稿](examples/meeting.txt) · [贡献指南](CONTRIBUTING.md) · [MIT 许可证](LICENSE)

## 快速开始

1. 打开在线工作台，用邮箱和密码注册，然后创建项目。
2. 在「模型设置」添加自己的 API Key。支持 OpenAI、Anthropic Claude、Google Gemini、DeepSeek 和阿里云百炼通义千问。
3. 导入会议逐字稿，每行一段。可以从[虚构示例](examples/meeting.txt)开始。
4. 在会议详情选择模型并运行分析。点击段落编号核对原文。

```text
00:01:30 产品同学：我负责在本周五前整理三条测试路径。
```

## 能做什么

- 提取决策、风险和行动项，并保留段落编号、发言人和原文。
- 在看板中按用户洞察、产品开发、市场营销、销售转化追踪事项及版本。
- 在会议详情查看发言段落分布和待核验的插话线索。

导入内容为文本，每场最多 45,000 字、500 段。模型调用会把待分析文本发送到所选服务商，并按该服务商规则计费。关键结论、责任人和日期请结合原文核对。

## 开发与部署

前端和 API 位于 `worker/`，数据库迁移位于 `drizzle/`。用 Node.js 24 和 Bash 运行源码检查与构建。

```bash
git clone https://github.com/RufusMacintosh/meeting-retrospective-agent.git
cd meeting-retrospective-agent
node tests/smoke.mjs
npm run build
npm run validate
```

测试用 SQLite 和模拟模型响应验证账户隔离、证据校验、版本历史和体验账号密钥隔离。构建生成 `dist/server/index.js`。部署时需要支持 Cloudflare Workers 的环境，将 D1 数据库绑定为 `DB`，按顺序执行 `drizzle/` 下的 SQL，并将 32 字节的随机值编码为 Base64URL 后作为 `KEY_ENCRYPTION_SECRET` 运行时密钥配置。运行时密钥由部署者生成。

`legacy/` 保留早期 Python 分析引擎、看板模板和数据契约，可用于阅读和二次开发。

## 欢迎贡献

欢迎使用、分享和改进。遇到问题可以提 [Issue](https://github.com/RufusMacintosh/meeting-retrospective-agent/issues)，有修改可以提 Pull Request。如果这个工具帮你节省了复盘时间，欢迎点 Star。

An open-source meeting review workspace. Paste a transcript, connect your own model API key, and trace decisions and action items back to the original speaker and segment. Contributions in Chinese or English are welcome.

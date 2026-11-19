# 处理规则与关键接口字段

> 规则实现集中在 `agent/rules.py`（纯函数）与 `agent/quality.py`（规范检测），
> 合并与状态机在 `agent/store.py`，接口在 `agent/api.py`。
> 所有阈值均为常量，可按项目词表调整，便于审计与单测。

---

## 一、处理规则

### 1. 分类规则

**模块分类**（`classify_module`）：对四模块关键词表分别计数，取命中最多者；无命中时回退 `product_dev` 并把置信度降到 `0.35`，同时在 `missing_fields` 标注「模块需人工确认」。

| 模块 | 典型关键词 |
|---|---|
| `user_insights` | 用户、客户、调研、访谈、画像、痛点、反馈、留存 |
| `product_dev` | 开发、技术、接口、API、版本、上线、PRD、测试、bug、灰度、联调 |
| `marketing` | 营销、市场、推广、活动、内容、投放、渠道、品牌、小红书 |
| `sales_conversion` | 销售、转化、线索、合同、报价、签约、客单价、试用、续约 |

**类别分类**（`classify_category`）：对四类关键词计数，平局时按 `blocker > progress > feedback > action` 优先级裁决；无命中回退 `progress` 且置信度 `0.35`。

| 类别 | 典型信号 |
|---|---|
| `blocker` | 卡在、阻塞、风险、问题、无法、延期、瓶颈、待定、不确定 |
| `progress` | 完成、上线、发布、交付、已解决、落地、跑通、已经 |
| `feedback` | 反馈、建议、意见、投诉、用户说、客户说、抱怨 |
| `action` | 负责、跟进、推进、安排、我来、落实、输出、牵头、评估 |

### 2. 抽取规则

| 字段 | 规则 | 抽不到时 |
|---|---|---|
| `owner` | ① `@姓名`；② `由X负责/牵头`、`负责人是X`；③ `X来/去负责…`（**必须用 `participants` 校验人名**）；④ `我来/我负责…` → 发言人 | `null` + `MissingInfo` |
| `due_date` | 正则匹配绝对日期、`N月N日`、`周X前`、`月底/季度末`、`N天内`、`今天/明天+前` 等；**保留原文相对表述** | `null` + `MissingInfo` |
| `priority` | 紧急/尽快/P0 → `high`；不急/P2 → `low`；否则 `normal` | `normal` |
| 证据 | 每条事项必带 `EvidenceRef`（段落 ID + 原文摘录），无证据的条目丢弃 | — |

> 人名校验很关键：否则「数据源清单我来梳理」会被误抽成责任人"数据源清单我"。

### 3. 状态更新规则（`infer_status`）

从后续会议发言中的信号词推断状态迁移，取"最强"信号：
`done`（已完成/通过了/跑通了…）> `cancelled`（取消/不做了…）> `blocked`（被阻塞/推迟/还在等…）> `in_progress`（进行中/正在做…）。
无信号则沿用原状态，不擅自变更。状态变化记入 `status_changed` 版本并附触发词。

### 4. 跨会议去重合并规则

`match_score` 四项融合（0~1），模块不同直接判 0：

| 分量 | 权重 | 含义 |
|---|---|---|
| 字符序列相似度 | 0.25 | 语序一致性 |
| 2-gram Jaccard | 0.10 | 主题重合比例 |
| 共享 2-gram 锚点 | 0.30 | 共享数 ÷ 8 封顶，捕捉核心术语复用 |
| 最长公共子串锚点 | 0.25 | 连续 ≥6 字相同即强信号 |
| 正文覆盖率 | 0.10 | 候选正文覆盖既有标题核心 n-gram 的比例 |

`MERGE_THRESHOLD = 0.58`，达到阈值即合并到既有 `matter_id`，并追加证据与 `merged_from`。

### 5. 冲突与缺失处理

- **填充**：旧值为空、新值有值 → 填充并移除对应 `MissingInfo`。
- **冲突**：两者都有值且不同 → 登记 `Conflict` 候选（值 + 各自证据 + 会议），**保留原值、不裁决**。
- **优先级**：只升级不降级。
- **类别演化**：如「行动 → 进度」，以置信度较高者为准更新，并记入版本差异。

### 6. 会议规范检测规则

| 维度 | 口径 |
|---|---|
| 议程推进 | 议程 2-gram 与发言 2-gram 重叠率 ≥ `0.34` 视为已讨论；`covered/总数` 为覆盖度 |
| 结论明确度 | `0.4×责任人填充率 + 0.3×截止填充率 + 0.3×min(1, 结论信号数/3)` |
| 跑题 | 字数 ≥15、与主题词（标题+议程+会议内复现≥2次的 2-gram）重叠率 < `OFF_TOPIC_THRESHOLD=0.10`；排除插话与主持人控场发言 |
| 打断 | 显式信号词（等一下/插一句/我补充一下…）或不同发言人在 `<3s` 内切换 |
| 发言失衡 | `balance = 1-(最大占比-理想占比)/(1-理想占比)`；`<0.5` 报问题 |
| 事项未闭环 | 状态非 `done`/`cancelled` 的事项数 |

每条问题都写入 `evidence_index`（`issue-NN` → `segment_id[]`），看板可一键查看原文。

---

## 二、关键接口字段

统一响应包装：`{ "code": 0, "message": "ok", "data": ... }`；失败为 HTTP 4xx/5xx。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/schemas/{name}` | 获取 JSON Schema |
| POST | `/api/meetings/ingest` | 摄入单场逐字稿（增量，可重复调用） |
| GET | `/api/meetings` | 会议列表 |
| GET | `/api/meetings/{id}/quality` | 该场会议规范分析 |
| GET | `/api/retrospective` | 完整项目复盘（四模块） |
| GET | `/api/board` | **看板主载荷（16 列 + 规范 + 统计）** |
| GET | `/api/matters` | 事项列表，支持 `module` / `category` / `status` 过滤 |
| GET | `/api/matters/{id}` | 单一事项详情（含 history） |
| GET | `/api/matters/{id}/history` | 单一事项完整版本历史 |
| GET | `/api/matters/{id}/history/{version}` | 指定版本快照 |
| GET | `/api/matters/{id}/evidence` | 证据链列表 |
| GET | `/api/matters/{id}/evidence/{segment_id}` | **按段落精确定位回原文** |

### 2.1 `POST /api/meetings/ingest`

请求体即 `Transcript` JSON（见 `transcript.schema.json`）。响应 `data`：

```json
{
  "meeting_id": "MTG-003",
  "date": "2026-01-20",
  "drafts": 7, "created": 2, "merged": 5,
  "changes": [
    {"matter_id": "M-0011", "action": "status_changed", "title": "...", "match_score": 0.72}
  ],
  "ingested_at": "..."
}
```

### 2.2 `GET /api/board`

响应 `data` 即 `BoardPayload`：

- `columns[16]`：每列 `module` / `category` / `label` / `items`（`Matter[]`，不含 history）
- `meetings[]`：`MeetingQuality[]`
- `stats`：含 `matter_count` / `merge_count` / `conflict_count` / `missing_field_count` / `closed_rate` 等

### 2.3 `GET /api/matters/{id}/history`

响应 `data` 为 `MatterRevision[]`，每项含 `version` / `change_type` / `snapshot` / `changes` / `meeting_id` / `raised_by`，可直接渲染时间线。

### 2.4 `GET /api/matters/{id}/evidence/{segment_id}`

响应 `data` 含 `matter_id` / `meeting_id` / `meeting_title` / `segment_id` / `speaker` / `timestamp` / `order` / `quote`，用于核验。

---

## 三、前端消费路径建议

1. 首屏 `GET /api/board` 渲染 16 列与统计。
2. 点击卡片 → 用载荷中的 `Matter` 渲染详情；异步取 `GET /api/matters/{id}/history` 渲染版本时间线。
3. 点击「定位回原文」→ `GET /api/matters/{id}/evidence/{segment_id}` 获取原文；看板演示版直接内嵌逐字稿并展示上下文。
4. 点击规范问题中的段落按钮 → 用 `meeting_id + segment_id` 打开原文核验弹窗。
5. 历史查询 → 按 `matter_id` 调 `GET /api/matters/{id}/history`，或按标题关键词在已加载的 `columns` 中过滤。

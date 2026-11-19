# 数据模型详解

> 唯一事实来源：`agent/models.py`；对外契约：`agent/schemas.py` 与 `data/output/schemas/*.json`。
> 命名约定：字段一旦发布只追加、不改名、不删除。所有时间字段用 ISO 8601 / `YYYY-MM-DD`。

---

## 1. 层次总览

| 层次 | 实体 | 作用 |
|---|---|---|
| 输入 | `Segment` / `Meeting` | 语音转写逐字稿（已区分发言人） |
| 抽取中间态 | `Matter` 草稿 | 每条草稿携带证据，待合并 |
| 核心实体 | `Matter` | 跨会议稳定事项，承载状态、版本、冲突、缺失 |
| 支撑 | `EvidenceRef` / `MatterRevision` / `Conflict` / `MissingInfo` | 证据链、版本快照、显式标注 |
| 输出 | `MeetingQuality` / `Retrospective` / `BoardPayload` | 规范分析、复盘、看板载荷 |

---

## 2. 输入模型

### 2.1 `Segment`（发言段落）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `segment_id` | string | ✅ | 会议内唯一段落 ID |
| `speaker` | string | ✅ | 发言人（转写软件已区分） |
| `text` | string | ✅ | 转写原文，**逐字保留不改写** |
| `timestamp` | string \| null | — | 如 `00:12:34` 或区间 `00:12:34-00:13:10` |
| `order` | integer | — | 段落序号；无时间戳时的定位兜底 |

### 2.2 `Meeting`（单场会议逐字稿）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `meeting_id` | string | ✅ | 会议唯一 ID |
| `project_id` | string | ✅ | 所属项目；同一项目多场会议靠此聚合 |
| `title` | string | — | 会议标题，参与跑题判定的主题词 |
| `date` | string (date) | ✅ | 会议日期，决定摄入顺序与版本时间 |
| `participants` | string[] | — | 参会人；**用于校验人名，防止把普通词误当责任人** |
| `agenda` | string[] | — | 议程，用于议程推进分析 |
| `segments` | `Segment[]` | ✅ | 逐字稿段落 |
| `source` | string \| null | — | 转写软件来源，便于溯源 |

---

## 3. 核心实体：`Matter`（事项）

`matter_id` 首次创建时生成（形如 `M-0011`），后续任何合并与更新都沿用，是前端稳定 key。

| 字段 | 类型 | 说明 |
|---|---|---|
| `matter_id` | string | 跨会议稳定 ID |
| `project_id` | string | 所属项目 |
| `module` | enum | `user_insights` / `product_dev` / `marketing` / `sales_conversion` |
| `category` | enum | `progress` / `blocker` / `action` / `feedback` |
| `title` | string | 从原文提炼的短标题 |
| `description` | string | 触发该事项的原文片段 |
| `status` | enum | `open` / `in_progress` / `done` / `blocked` / `cancelled` |
| `owner` | string \| null | 责任人；无明确表达时为 `null` 并在 `missing_fields` 标注 |
| `due_date` | string \| null | 截止时间；**保留原文相对表述**（如"下周五前"） |
| `priority` | enum | `low` / `normal` / `high` |
| `confidence` | number(0~1) | 抽取置信度，看板用进度条展示 |
| `tags` | string[] | 标签 |
| `evidence` | `EvidenceRef[]` | 证据链（可多条，跨会议合并后累积） |
| `history` | `MatterRevision[]` | 不可变版本列表；`get_matter(include_history=False)` 时省略 |
| `conflicts` | `Conflict[]` | 互斥取值标注 |
| `missing_fields` | `MissingInfo[]` | 缺失字段标注 |
| `first_seen_meeting` / `first_seen_date` | string | 首次发现的会议与日期 |
| `last_seen_meeting` / `last_updated_date` | string | 最近出现 / 最近更新的会议与日期 |
| `merged_from` | string[] | 被合并进来的草稿 ID；长度 >1 即发生过合并 |
| `revised_count` | integer | 版本数 - 1 |

### 3.1 `EvidenceRef`（证据引用，核验核心）

| 字段 | 说明 |
|---|---|
| `meeting_id` / `meeting_title` | 来源会议 |
| `segment_id` | 段落 ID，定位主键 |
| `speaker` | 发言人 |
| `timestamp` | 时间戳（可空） |
| `order` | 段落序号（无时间戳时的定位兜底） |
| `quote` | **逐字摘录**，不做改写 |

看板「定位回原文（含上下文）」即用 `meeting_id + segment_id` 回查逐字稿，并展示前后各一段上下文。

### 3.2 `MatterRevision`（版本快照）

| 字段 | 说明 |
|---|---|
| `version` | 从 1 递增，不可变 |
| `meeting_id` / `meeting_date` | 触发变更的会议 |
| `change_type` | `created` / `updated` / `status_changed` / `merged` / `confirmed` |
| `note` | 人类可读说明（含触发信号词或相似度） |
| `snapshot` | 该版本**全部业务字段**快照，可还原任意历史状态 |
| `changes` | `FieldChange[]`（`field` / `old_value` / `new_value`） |
| `raised_by` | 触发变更的发言人 |

### 3.3 `Conflict` 与 `MissingInfo`

`Conflict`：`field`、`candidates`（每项含 `value` / `evidence` / `meeting_id`）、`note`、`resolved`、`resolution`。
系统**不自动裁决**，保留所有候选值及各自证据。

`MissingInfo`：`field`、`reason`、`evidence`、`needs_confirmation`。
示例：`owner` 缺失时 reason 为"原文未出现明确责任人（无 @/由X负责/我来 等表达）"。

---

## 4. 输出模型

### 4.1 `MeetingQuality`（单场规范分析）

| 字段 | 说明 |
|---|---|
| `agenda_progress[]` | 每项议程：`agenda_index` / `agenda` / `covered` / `discussed_segments` / `note` |
| `agenda_coverage` | 议程覆盖度 0~1 |
| `decision_clarity` | `action_count`、`owner_defined_rate`、`due_defined_rate`、`decision_signal_count`、`decision_segments`、`clarity_score` |
| `off_topic[]` | `segment_id` / `speaker` / `timestamp` / `quote` / `reason` |
| `interruptions[]` | `at_segment_id` / `interrupter` / `interrupted` / `timestamp` / `quote` / `signal` |
| `speaker_stats[]` | `speaker` / `segment_count` / `word_count` / `share` |
| `speaker_balance` | 发言均衡度 0~1（1 为完全均衡） |
| `open_item_count` | 未闭环事项数（状态非 done/cancelled） |
| `issues[]` | 人类可读问题清单 |
| `evidence_index` | `issue-01` → `segment_id[]`，让每条问题可回原文 |

### 4.2 `Retrospective`（项目复盘）

```
{
  project_id, generated_at, source_meeting_ids,
  modules: {
    user_insights:   { progress:[Matter], blocker:[Matter], action:[Matter], feedback:[Matter] },
    product_dev:     { ... },
    marketing:       { ... },
    sales_conversion:{ ... }
  },
  stats: { ... }
}
```

### 4.3 `BoardPayload`（看板直接消费）

| 字段 | 说明 |
|---|---|
| `columns[]` | 4 模块 × 4 分类 = **16 列**：`module` / `category` / `label` / `items`（`Matter`，不含 history） |
| `meetings[]` | `MeetingQuality[]`，驱动「规范分析」视图 |
| `stats` | `matter_count` / `meeting_count` / `by_status` / `by_module` / `conflict_count` / `missing_field_count` / `evidence_count` / `merge_count` / `quality_issue_count` / `closed_rate` |

> 看板详情所需的历史从 `matter_histories.json`（或 `GET /api/matters/{id}/history`）按 `matter_id` 单独取，避免看板主载荷过大。

---

## 5. Schema 文件索引

| 文件 | 描述 |
|---|---|
| `transcript.schema.json` | 输入逐字稿契约 |
| `matter.schema.json` | 核心事项契约（含证据、版本、冲突、缺失） |
| `retrospective.schema.json` | 复盘输出契约 |
| `board.schema.json` | 看板载荷契约 |
| `quality.schema.json` | 规范分析契约 |

全部位于 `data/output/schemas/`，可通过 `GET /api/schemas/{name}` 获取。

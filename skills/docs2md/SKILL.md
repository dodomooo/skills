---
name: docs2md
description: >
  将企业级 .doc/.docx 文档（IPD 体系需求规格说明书、概要设计说明书等）转换为 AI 可读的结构化 Markdown。
  采用两阶段管道：阶段1 用 Pandoc + Lua filter + Python 正则脚本做机械清洗，阶段2 由 AI 做语义优化。
  当用户提到"docx 转 markdown""doc 转 markdown""文档转换""docx2md""把 word 转成 md""转换需求文档""转换设计文档"时触发。
  即使用户只是说"帮我转一下这个 doc/docx"或给出一个 .doc/.docx 文件路径，也应触发此 skill。
  不要用于创建或编辑 Word 文档——此 skill 只做 doc/docx → markdown 方向的转换。
---

# docs2md：企业 doc/docx → 结构化 Markdown 转换

## 概述

将 `.doc` / `.docx`（IPD 体系文档）转换为结构化 Markdown，重点是**信息抽取与结构重组**，而非版式还原。

两阶段管道：

```
.doc
  → 预处理（脚本）: 调用 `scripts/doc_to_docx_wps.py` 另存为 `.docx`
.docx
  → 阶段1（脚本）: Pandoc 转换 + 表格标准化 + 正则清洗  →  .md + .scan.json
  → 阶段2（AI）:  靶向语义优化（仅修改风险标记区域）
  → 清理：删除 .scan.json
```

## 前置依赖

- **Pandoc**：必须已安装并在 PATH 中可用（`pandoc --version` 可执行），且支持 Lua filter
- **Python 3** + `pyyaml`：如缺少则 `pip install pyyaml`

如果 Pandoc 不可用，告知用户安装后重试。

## 调用参数

用户调用 skill 时可传入以下参数：

| 参数 | 说明 | 示例 |
|------|------|------|
| `--config <path>` | 指定配置文件路径（默认使用 skill 目录下的 `config.yaml`） | `/docs2md myfile.doc --config custom.yaml` |
| `--report` | 生成 JSON 转换报告（默认不生成） | `/docs2md myfile.docx --report` |
| `-o <dir>` | 指定 MD 输出目录（默认 `md/`） | `/docs2md myfile.doc -o output/` |

> **JSON 报告默认不生成**。需要时由用户显式传入 `--report`。

## 执行流程

### 步骤 1：确定待转换文件

- 用户可能给出单个文件路径、目录路径或文件列表
- 如果给出目录，递归扫描其中所有 `.doc` / `.docx` 文件（包含嵌套子目录，跳过 `~$` 开头的 Office 临时文件）
- 确认文件列表后开始逐文件处理

### 步骤 2：逐文件执行阶段1（脚本转换）

调用本 skill 自带的转换脚本（默认输出到 `md/`）：

```bash
python <skill-path>/scripts/docs2md.py <input.doc|input.docx> -o md/
```

如用户传入 `-o <dir>`，则使用指定目录；如用户传入 `--report`，则同时添加该参数：

```bash
python <skill-path>/scripts/docs2md.py <input.doc|input.docx> -o md/ --report
```

脚本会：
1. 如果原文件是 `.doc`，先调用 `scripts/doc_to_docx_wps.py` 另存为 `.docx`，并保留该 `.docx`
2. 调用 Pandoc 做粗转换，并通过 `scripts/gfm-table-normalize.lua` 将表格标准化为 GFM pipe table
3. 从 TOC 提取标题编号映射
4. 移除前页（封面、审批表、修订记录、目录）
5. 执行正则清洗（噪音移除、实体处理、标题空白规范化）
6. 还原标题编号（TOC 映射法 → 层级计数法 → 不还原）
7. 插入文档级 H1 标题（文件名去扩展名）
8. 标题层级整体下移一级（H1→H2, H2→H3, ...）
9. 扫描 `.docx` 包中的嵌入附件/OLE 对象（如 `.xlsx`、`.docx`、`.pdf`、`oleObject*.bin`），仅记录风险，不自动提取或转换附件内容
10. 输出 `.md` 文件和 `.scan.json` 风险索引（供阶段2 靶向处理）

脚本输出路径：
- 单文件输入：`<output-dir>/<原文件名>.md`
- 目录输入：`<output-dir>/<相对子目录>/<原文件名>.md`
- `<output-dir>/<相对子目录>/<原文件名>.scan.json`（风险索引）

**如果脚本失败**：检查错误信息。如果是 Pandoc 未安装，提示用户安装。如果是文件损坏，脚本会尝试 OOXML 降级提取纯文本。

### 步骤 3：执行阶段2（AI 后处理）

#### 核心约束（必须遵守）

1. **不修改任何实质内容**——仅做格式优化
2. **不新增**原文不存在的结论或归纳
3. **不改写**术语、数字、日期、约束条件
4. 文档级 H1 标题由脚本生成，AI 不额外新增或修改
5. 仅在存在**明确结构错误**时调整标题层级，不因美观主动调整
6. 无法判断时**默认保留并标记**，宁可多保留不可误删

#### 3a. 选择处理路径

检查脚本是否生成了 `.scan.json`（与 `.md` 同目录）：

- **`.scan.json` 存在且 `total_risks > 0`**：按「3b. 靶向修复」处理
- **`.scan.json` 存在且 `attachments.total > 0`**：提示用户文档包含嵌入附件，附件内容未进入 Markdown；如用户需要附件内容，应单独提取或转换附件
- **`.scan.json` 存在且 `total_risks == 0` 且 `attachments.total == 0`**：无需 AI 处理，`.md` 已是最终输出，删除 `.scan.json` 即可
- **`.scan.json` 不存在**（旧版脚本兼容）：按「3c. 回退方案」处理

#### 3b. 靶向修复协议

**第一步：读取风险索引**

读取 `.scan.json`，了解 Markdown 风险数量（`total_risks`）、区域数量（`total_regions`）、附件风险数量（`total_attachment_risks`）和分布。若 `attachments.total > 0`，先向用户提示附件未转换；附件风险不通过 region 修复。

**第二步：按 region 逐组处理**

遍历 `.scan.json` 中的 `regions` 数组，对每个 region：

1. 使用 Read 工具的 offset/limit 参数读取 `.md` 文件的该区域：
   `Read path=<name>.md offset=<region.start_line> limit=<region.end_line - region.start_line>`

2. 根据区域内的风险类型（`types` 字段）执行对应操作：

   - **paragraph_merge**：检查标记行与上一行是否构成语义连续
     （两行在语义上构成连续表述，且前行无终结标点、后行非结构标记开头），
     若是则用 Edit 工具合并（将换行替换为空格或直接拼接）

   - **grid_table / old_style_table**：脚本阶段表格标准化后的异常残留。
     读取完整表格区域；若能确认是简单残留且不改变实质内容，可用 Edit 修正为 GFM pipe table；
     若涉及复杂合并关系或无法确认，保留原文并记入 `attention`

   - **heading_jump**：检查上下文确认是否为真正跳级，
     若确认则用 Edit 修正标题层级

   - **pandoc_annotation / image_remnant / anchor_remnant**：
     用 Edit 删除残留噪音

   - **table_alignment**：脚本阶段表格标准化后的异常残留。
     仅在列数错误明确且可机械修正时用 Edit 修正，否则记入 `attention`

   - **italic_missing**：读取所在章节上下文，判断是否为模板指导语，
     若是则用 Edit 补充斜体标记 `*...*`

3. 每次 Edit 操作后继续处理下一个 region

**附件风险处理**

若 `.scan.json.attachments.total > 0`，不修改 `.md`。根据 `.scan.json.attachments.items` 向用户提示嵌入附件路径、类型和“未转换进 Markdown”的事实；如需要附件内容，需另行提取或转换附件源文件。

**第三步：清理**
- 删除 `.scan.json`
- 如有 `--report`，在 JSON 报告中记录修复项

> **注意**：靶向模式下 AI 只读取标记区域，无法发现 scan_risks() 未覆盖的问题。
> 对于关键文档，用户可要求"全文审查"以获得完整覆盖。

> **subagent 加速**（可选）：当 `total_regions > 15` 时，可使用 subagent 分担分析工作。
> 禁止多个 subagent 并行 Edit 同一文件。安全模式：每个 subagent 负责一组 regions，
> 将修复建议以 JSON 返回（不直接 Edit），主 agent 收集后按行号从大到小依次 Edit。

#### 3c. 回退方案（无 .scan.json）

当脚本版本较旧未生成 `.scan.json` 时：

1. 尝试直接 Read `.md`
2. 若 Read 成功（小文件）：整文件执行以下语义优化后覆盖写入 `.md`
3. 若 Read 报错超限（大文件）：
   - 用 Grep 定位 H2 行号：`Grep pattern="^## " path=<name>.md output_mode="content" -n=true`
   - 计算各节行范围
   - 逐节 Read offset/limit → 处理 → 追加写入 `.md`

#### 处理范围（3b 靶向修复和 3c 回退方案共用）

1. **断裂段落合并**：软回车导致的段落拆分，将属于同一语义段落的多行合并为一段。判断依据：
   - 前一行不以句号、分号等终结标点结尾
   - 后一行不以标题标记、列表标记、表格分隔符开头
   - 两行在语义上构成连续表述

2. **标题层级修正**：仅在发现明确跳级错误时修正（如 H2 直接到 H4 且缺少 H3）

3. **残留噪音识别**：脚本未覆盖的边缘模式（如非标准格式的封面残留），识别后移除

4. **表格异常残留处理**：阶段1 已将表格标准化为 GFM pipe 格式；若仍残留 grid table / old-style table / 列对齐错误，视为脚本异常或复杂表格降级风险，能机械修正时修正，无法确认时记入 `attention`

5. **模板指导语斜体还原**：同一章节内其他指导语为斜体时，补充遗漏的斜体标记

6. **可疑内容记录**：发现可疑问题时记入报告的 `attention` 字段，不默认修改

#### 输出

将优化后的内容写入最终 `.md` 文件：
- 输出路径：`<output-dir>/<原文件名>.md`（output-dir 默认为 `md/`）
- 删除 `.scan.json`

### 步骤 4：生成 JSON 报告（仅当用户传入 `--report` 时）

为每个文件在 `<output-dir>/reports/` 下生成报告：

```json
{
  "file_name": "文件名.doc",
  "conversion_result": "success",
  "script_result": {
    "status": "success",
    "output_md": "<output-dir>/文件名.md",
    "preconverted_docx": "文件名.docx"
  },
  "ai_result":     { "status": "success", "output_md": "<output-dir>/文件名.md" },
  "attention": []
}
```

`attention` 数组记录需要人工关注的问题，如标题编号异常、层级超限、可疑内容、嵌入附件未转换等。仅在失败、跳过或存在疑点时增加对应字段。
`script_result.preconverted_docx` 仅在输入为 `.doc` 且预转换成功时出现。

### 步骤 5：输出批次汇总

所有文件处理完毕后，输出汇总：
- 成功/失败计数
- 每个文件的 attention 项（如有）
- 失败文件的错误原因

#### 快速验收检查

对每个产出 `.md`，确认：
1. 首行为 `# 原文件名`（H1 文档标题）
2. 正文标题从 H2 开始，无 H1 级正文标题
3. 无 `![`、`{.mark}`、`{.underline}`、`[]{#` 残留
4. 表格均为 GFM pipe 格式（无 `+---+`、`--- ---` 或 HTML `<table>`）
5. 若 `.scan.json.attachments.total > 0`，已提示用户附件内容未进入 Markdown
6. 实质内容未被修改（仅格式变化）

## 详细规则参考（按需查阅，勿预先全部读取）

以下文件是阶段1 脚本的设计文档和输出规范参考。阶段2 正常处理中，靶向修复协议已内含足够的操作指引，无需预先读取。仅在以下情况查阅：

- 脚本行为异常需要排查 → `references/conversion-rules.md`、`references/cleanup-rules.md`
- 需确认完整输出规范或验收标准 → `references/markdown-output-spec.md`

## 配置

`config.yaml` 存放在 skill 目录内，控制转换行为的细节。这是 skill 内部实现的一部分，不会输出到结果目录。

配置项和默认值见 skill 目录下的 `config.yaml`。通常不需要修改，除非目标文档的封面/审批页使用了非标准关键词。

主要可配置项：
- **front_page**：封面检测关键词（cover/approval/revision）、TOC 标识词、行长度阈值、连续文本阈值
- **cleanup**：各正则清洗规则的开关、Pandoc 标注类型列表、空行压缩阈值
- **heading**：标题层级下移量、最大层级

## 输出文件命名

默认输出到当前工作目录下的 `md/` 子目录，输出文件沿用原始 `.doc` / `.docx` 文件名，仅替换扩展名：

```
XX项目_系统需求规格说明书_V6.0.1.doc
  → XX项目_系统需求规格说明书_V6.0.1.docx          （.doc 预转换产物，保留）
  → md/XX项目_系统需求规格说明书_V6.0.1.md       （最终输出）
  → md/XX项目_系统需求规格说明书_V6.0.1.scan.json （风险索引，处理完删除）
  → md/reports/XX项目_系统需求规格说明书_V6.0.1.json  （仅当 --report 时生成）
```

目录批量转换会保留输入目录的相对层级，避免不同子目录中的同名文件互相覆盖：

```
docs/
└── 子目录/
    └── 需求说明书.docx

md/
└── 子目录/
    ├── 需求说明书.md
    └── 需求说明书.scan.json
```

## 容错

- **Pandoc 失败**：降级为 OOXML 直接解析（仅提取纯文本，丢失结构），脚本内置此逻辑
- **单文件失败不中断批次**：记录错误到 JSON 报告，继续处理下一个文件
- **编码**：`.doc` 先另存为 `.docx`，`.docx` 按 ZIP+XML 处理；Pandoc 产出统一 UTF-8
- **中文路径与文件名**：脚本使用绝对 `Path` 和参数列表调用子进程，支持中文目录、中文文件名和嵌套子目录
- **嵌入附件**：`.docx` 按 ZIP+OOXML 扫描 `word/embeddings/` 和关系文件；直接嵌入的 `.xlsx`、`.docx`、`.pdf` 等可记录扩展名，`oleObject*.bin` 仅能稳定识别为 OLE 嵌入对象，需人工确认原始类型

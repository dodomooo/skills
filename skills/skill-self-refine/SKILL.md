---
name: skill-self-refine
description: 编排 Agent Skill 的创建、验证、改进、回归检查和发布闭环。当用户需要创建新 Skill、优化已有 Skill、检查 Skill 回归、发布 Skill 版本时使用。自动调用 skill-creator 完成底层评测，在此基础上增加 golden_set 回归守卫、迭代熔断和版本发布管理。支持通过 --output-dir、--max-iterations 等参数自定义流水线行为。
---

# Skill Self Refine

你是 Agent Skill 工程闭环的编排器。你负责调度整个流水线：接收用户意图 → 路由任务类型 → 调用 skill-creator → 回归守卫 → 人工门禁 → 发布。

## 适用范围

本 Skill 仅适用于 Agent Skill 的创建、改进、回归检查和发布。具体包括：
- SKILL.md / skill definition 的编写和优化
- 配套 references、scripts、assets 的管理
- evals 测试用例和 golden_set 管理
- CHANGELOG 和版本发布

**不适用：** 普通应用代码、业务系统、普通文档、模型微调、非 Skill 组件。遇到这些输入时，停止并说明不在适用范围内。

## 核心原则

1. **提示词是指导，不是强约束** — 用判断力，不要死板执行
2. **强约束必须用代码实现** — scripts/ 下的脚本是真正的执行者
3. **通过 `Skill(skill="skill-creator")` 调用底层能力** — 不要手动重复实现 skill-creator 的功能
4. **所有自动循环必须有预算、终止条件和熔断机制**
5. **版本发布必须经过人工确认**

## 任务路由

读取用户输入，识别任务类型。参考 `references/task_types.md` 获取详细的识别规则。

### 类型 1：创建新 Skill

**触发特征：** 用户描述了需求但未指定已有 Skill 路径，或指定路径不存在。

**流程：**
1. 确认需求（目标、触发场景、输出格式、是否需要测试用例）
2. 调用 `Skill(skill="skill-creator")` 完成创建、评测、改进循环
3. 消费 skill-creator 产物（benchmark.json 等）
4. 运行 `scripts/check_regression.py`（首次创建无 golden_set，自动跳过并将当前结果提升为 golden_set）
5. 启动 eval-viewer → 用户审查输出 → 读取 feedback.json
6. 展示发布确认汇总 → 用户决策（发布 / 继续改进）
7. 发布

### 类型 2：改进已有 Skill

**触发特征：** 用户指定了已有 Skill 路径 + "改进"/"优化"/"fix"/"修" 等关键词。

**流程：**
1. 备份当前 Skill 到 `{output_dir}/{skill-name}/backup-{timestamp}/`
2. 调用 `Skill(skill="skill-creator")` 进行改进和评测
3. 运行 `scripts/check_regression.py --benchmark <新benchmark> --golden-set <skill-path>/evals/golden_set.json`
4. 运行 `scripts/circuit_breaker.py check` 检查熔断状态
5. 回归或熔断 → 阻止发布，输出具体原因
6. 通过 → 启动 eval-viewer → 用户审查输出 → 读取 feedback.json
7. 展示发布确认汇总 → 用户决策（发布 / 继续改进）
8. 发布

### 类型 3：回归检查

**触发特征：** 用户指定已有 Skill 路径 + "检查"/"回归"/"验证" 等关键词，无改进意图。

**流程：**
1. 调用 `Skill(skill="skill-creator")` 对当前 Skill 跑完整评测
2. 运行 `scripts/check_regression.py` 对比 golden_set
3. 输出结果报告（不进入发布流程）

## 双层迭代模型

本 Skill 和 skill-creator 各自有一个迭代循环，层级不同：

| 层级 | 管理者 | 范围 | 控制参数 |
|------|--------|------|----------|
| **内层循环** | skill-creator | 单次会话内的 draft → test → review → improve → test ... | skill-creator 自行管理 |
| **外层循环** | skill-self-refine | 每轮 = 一次完整的 skill-creator 会话 → 回归守卫 → 人工门禁 | `--max-iterations` |

当用户说"最多迭代 3 轮"时，指的是**外层循环**——即最多调用 3 次 skill-creator 进行完整改进。每次 skill-creator 内部可能自身迭代多轮（取决于其逻辑和用户反馈）。如果你想让外层只执行一轮（即信任 skill-creator 一次完成），设 `--max-iterations 1`。

外层循环的终止条件：
- 用户通过人工门禁确认发布
- 达到 `--max-iterations` 上限
- 熔断触发（连续无改进或单次退化）

## 可配置参数

以下参数可通过对话指定，会传递给脚本的对应 CLI 标志：

| 参数 | CLI 标志 | 默认值 | 说明 |
|------|---------|--------|------|
| 输出目录 | `--output-dir` / `-o` | `./skill-refine-output/` | 所有中间产物和最终产物，自动按 skill 名称创建子目录 |
| 迭代预算 | `--max-iterations` / `-n` | `5` | 外层循环最大轮数 |
| 连续无改进 | `--no-improvement-limit` | `3` | 连续 N 次外循环无 pass_rate 提升则熔断 |
| 退化阈值 | `--pass-rate-drop` | `0.2` | 单次外循环 pass_rate 下降超此值则回退 |
| 回归容差 | `--pass-rate-tolerance` | `0.05` | golden_set pass_rate 允许下降容差 |

默认值定义在 `references/pipeline_config.json`。用户对话指定的值等价于 CLI 传参，优先级高于配置文件。

用例：用户说"最多迭代 3 轮，输出到 ~/output"，对应 `--max-iterations 3 --output-dir ~/output`。

**注意：** 调用脚本（`check_regression.py`、`circuit_breaker.py`）时，`--output-dir` 参数需拼接 `{skill-name}` 子目录，如 `--output-dir ./skill-refine-output/csv-converter/`。脚本本身不做自动拼接，由你在调用时按规范传入。

## 调用 skill-creator

创建和改进任务的核心执行由 skill-creator 完成。使用 Skill 工具调用：

```
Skill(skill="skill-creator")
```

skill-creator 会处理：
- 需求访谈和 SKILL.md 编写
- 测试用例生成
- 并行 subagent 执行（with-skill / baseline）
- 断言评分（grading.json）
- Benchmark 汇总（benchmark.json）
- 失败分析和迭代改进
- Description 优化

**你的职责是消费这些产物，而不是重复实现它们。**

skill-creator 执行完毕后，读取以下产物：
- `<workspace>/iteration-N/benchmark.json` → 回归守卫输入
- `<workspace>/iteration-N/grading.json` → 断言详情
- `<workspace>/iteration-N/benchmark.md` → 人类可读报告

## 产物验证

在进入回归守卫前，验证 skill-creator 产物完整性：

1. 检查 benchmark.json 存在且格式有效（参考 `references/golden_set_schema.md`）
2. 检查 grading.json 存在（行为 evals 场景）
3. 缺失时报告并终止，不进入后续步骤

## 回归守卫

运行 `python scripts/check_regression.py`：

```bash
python scripts/check_regression.py \
  --benchmark <path-to-benchmark.json> \
  --golden-set <skill-path>/evals/golden_set.json \
  --pass-rate-tolerance <value>
```

判定规则（三项 OR 关系）：
- 新 pass_rate < golden_pass_rate - tolerance
- 新 total_tokens > golden_token_mean × 1.3
- 新 total_duration_seconds > golden_time_mean × 1.5

首次创建时 golden_set 不存在 → 自动跳过，将当前 benchmark 提升为 golden_set（写入 `<skill-path>/evals/golden_set.json`）。

检测到回归 → 标记 FAILED，输出具体回归指标，阻止发布。通过 → 更新 golden_benchmark（不降低 golden_pass_rate）。

## 熔断检查

运行 `python scripts/circuit_breaker.py check` 检查熔断状态。该脚本维护 `{output_dir}/{skill-name}/.pipeline_state.json`。

三种熔断机制：
- **迭代预算：** 达到 `max_iterations`（外层循环上限） → 停止改进，取历史最佳进入门禁
- **连续无改进：** 连续 N 次外层循环 pass_rate 无提升 → 停止，保留原版本
- **单次退化：** 单次外循环 pass_rate 下降 > `pass_rate_drop` → 丢弃该版本，回退

熔断触发后，向用户报告具体原因和当前状态。

## 人工门禁

人工门禁分两步：先审查输出质量，再做发布决策。

### 步骤 1：启动 eval-viewer 供用户审查

skill-creator 完成后会产出 eval-viewer 所需的产物（benchmark.json + 各 eval 的输出文件）。启动 eval-viewer：

```bash
python {skill-creator-path}/eval-viewer/generate_review.py \
  <workspace>/iteration-<N> \
  --skill-name "<skill-name>" \
  --benchmark <workspace>/iteration-<N>/benchmark.json
```

如果是在无图形界面的环境（如 Cowork），使用 `--static <output_path>` 生成独立 HTML。

eval-viewer 有两个 tab：
- **Outputs** — 逐条展示每个测试用例的输入/输出，用户可以留下反馈
- **Benchmark** — pass_rate / time / tokens 的数值对比

用户审查完毕后点击 "Submit All Reviews"，eval-viewer 会生成 `feedback.json`。读取该文件获取用户对每个 eval 的具体反馈。空反馈表示用户认可。

### 步骤 2：发布确认

用户完成 eval-viewer 审查后，汇总以下信息向用户展示：

1. **eval-viewer 反馈摘要** — 用户标记的问题和认可点
2. **Benchmark 摘要** — pass_rate / time / tokens 三列，with-skill vs baseline delta
3. **回归检查结果** — 通过/失败 + 具体指标
4. **SKILL.md diff**（改进场景）
5. **拟生成的 CHANGELOG 条目**

用户操作：
- "确认发布" / "发布" → 进入发布流程
- "不要发布" / 说明调整方向 → 关闭 eval-viewer（`kill $VIEWER_PID`），进入下一轮外层循环（如未超预算）

## 发布

确认发布后执行：

### 1. 写回文件

将改进后的文件写回目标 Skill 目录。旧版本备份在 `{output_dir}/{skill-name}/backup-{timestamp}/`。

### 2. 更新 CHANGELOG

在 Skill 目录下创建或更新 CHANGELOG.md：

```markdown
# CHANGELOG

## v{N} ({YYYY-MM-DD})
- {变更描述}
- Benchmark: pass_rate {old}% → {new}%, time {old}s → {new}s, tokens {old} → {new}
```

版本号自动递增（首次为 v1）。

### 3. 打包

```bash
python {skill-creator-path}/scripts/package_skill.py {skill-path}
```

产物输出到 `{output_dir}/{skill-name}/{skill-name}.skill`。

### 4. 清理

- 保留 `{output_dir}/{skill-name}/backup-{timestamp}/`
- 保留 `{output_dir}/{skill-name}/{skill-name}.skill`
- 保留 `{output_dir}/{skill-name}/.pipeline_state.json`
- 其余临时文件可清理

## 错误处理

| 场景 | 处理 |
|------|------|
| skill-creator 不可用 | 报告 "skill-creator 未安装或不可用" 并终止 |
| 输入超出适用范围 | 停止并解释范围边界 |
| benchmark.json 缺失 | 报告 "skill-creator 未完成评测" 并要求重试 |
| golden_set 格式无效 | 报告具体格式错误，建议重新生成 |
| 脚本执行失败 | 输出 stderr，询问用户是否继续 |

## 产出文件结构

所有产物存放在 `{output_dir}/{skill-name}/` 下，不同 Skill 的产物互不干扰：

```
{output_dir}/{skill-name}/
├── .pipeline_state.json    # 熔断状态
├── workspace/               # skill-creator 评测产物
├── backup-{timestamp}/      # 旧版本备份
├── {skill-name}.skill       # 最终打包产物
└── CHANGELOG.md             # 变更记录副本
```

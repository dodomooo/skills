# Pipeline Config

流水线可配置参数说明。实际默认值定义在 `pipeline_config.json` 中，脚本直接读取该 JSON 文件。

CLI 参数优先于 JSON 默认值，用户对话中指定的值等价于 CLI 传参。

## 参数说明

### output_dir
- **类型:** string (路径)
- **CLI:** `--output-dir <path>` / `-o <path>`
- **默认:** `./skill-refine-output/`
- **说明:** 所有中间产物和最终产物的统一输出目录。支持相对路径和 `~`。实际产物会按 skill 名称自动创建子目录（如 `./skill-refine-output/csv-converter/`）。

### max_iterations
- **类型:** int
- **CLI:** `--max-iterations <n>` / `-n <n>`
- **默认:** `5`
- **说明:** 外部改进循环的最大轮数。每轮 = 一次完整的 skill-creator 会话（含其内部的 draft→test→review→repeat）。超限后停止改进，取历史最佳版本进入门禁。

### no_improvement_limit
- **类型:** int
- **CLI:** `--no-improvement-limit <n>`
- **默认:** `3`
- **说明:** 连续 N 次外循环无 pass_rate 提升则触发熔断，停止改进循环。

### pass_rate_drop
- **类型:** float
- **CLI:** `--pass-rate-drop <value>`
- **默认:** `0.2`
- **说明:** 单次外循环导致 pass_rate 下降超过此阈值时，丢弃该版本并回退。

### pass_rate_tolerance
- **类型:** float
- **CLI:** `--pass-rate-tolerance <value>`
- **默认:** `0.05`
- **说明:** golden_set 回归检查中 pass_rate 的允许下降容差。仅作用于 pass_rate 指标，不影响 token/time 的膨胀阈值。

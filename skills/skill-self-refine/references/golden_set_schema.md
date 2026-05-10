# golden_set.json Schema

golden_set.json 存储历史最佳评测基准，用于回归检查。

## 文件位置

```
<skill-path>/evals/golden_set.json
```

## JSON Schema

```json
{
  "golden_pass_rate": 0.85,
  "golden_token_mean": 45000,
  "golden_time_mean": 23.5,
  "source_benchmark": "/path/to/benchmark.json",
  "created_at": "2026-05-02T10:30:00Z",
  "eval_count": 4
}
```

## 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `golden_pass_rate` | float (0-1) | 历史最佳断言通过率。只升不降 |
| `golden_token_mean` | int | 历史最低平均 token 消耗 |
| `golden_time_mean` | float | 历史最短平均耗时（秒） |
| `source_benchmark` | string | 生成此 golden 的 benchmark.json 路径 |
| `created_at` | string | ISO 8601 时间戳 |
| `eval_count` | int | 评测用例数量 |

## benchmark.json 期望字段

check_regression.py 从 benchmark.json 中提取以下字段：

```json
{
  "summary": {
    "with_skill": {
      "pass_rate": 0.85,
      "total_tokens": 45000,
      "total_duration_seconds": 23.5
    }
  },
  "evals": [
    {
      "assertions": [
        {"passed": true},
        {"passed": false}
      ]
    }
  ]
}
```

如果 `summary.with_skill` 不存在，会回退到 `summary` 根级别查找。如果 `summary` 中也没有 `pass_rate`，会从 `evals[].assertions` 中计算。

## 回归判定

三项 OR 关系，任一项超标即判回归：

1. **pass_rate** < golden_pass_rate - tolerance（默认 tolerance=0.05）
2. **total_tokens** > golden_token_mean × 1.3
3. **total_duration_seconds** > golden_time_mean × 1.5

## golden_set 更新规则

- **首次创建：** benchmark.json 直接提升为 golden_set
- **通过回归检查后：** golden_pass_rate 只升不降；token/time 取较优值（更低更好）
- **回归检查失败：** golden_set 不更新

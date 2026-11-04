# 旅游助手评测工具

包含20条合成评测草稿：10条开发、10条留出，普通规划、数量、交通、偏好各5条，其中5条开发样例标记为pilot。这些不是用户日志或已人工确认的标准答案，完整人工审阅与留出集评测尚未完成。

cases.jsonl保留2026-11-04数据版本，出行日期从2026-11-05开始。长期使用前应建立新的日期版本，保留旧版本和运行记录；不要在两个对照版本之间偷偷修改题目。manifest部分notes是建立时的历史状态。

## 文件

| 文件 | 用途 |
| --- | --- |
| cases.jsonl、manifest.json | 题目、评分约定、版本和划分 |
| validate_cases.py | 离线检查20条题目和接口兼容性 |
| evaluators.py | 独立检查日期、数量、路线、步行、地点来源和预算 |
| run_eval.py | 预览、回放或显式运行真实后端，保存独立结果目录 |
| rubric.md | 评分标准与人工评分说明 |
| test_evaluation.py | 26项离线评测工具测试 |
| examples/synthetic_responses.jsonl | 合成回答，仅用于验证评分工具，不是真实Agent成绩 |

## 离线验证

以下命令从项目根目录进入backend后运行，假设虚拟环境位于项目根目录的.venv。普通校验和测试不调用模型或地图。

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -X utf8 -B evaluation/validate_cases.py
..\.venv\Scripts\python.exe -X utf8 -B -m unittest evaluation.test_evaluation
..\.venv\Scripts\python.exe -X utf8 -B evaluation/run_eval.py --pilot
```

最后一个命令仅预览，不请求后端、不创建结果。macOS/Linux将Python路径替换为../.venv/bin/python。

用合成回答演示评分：

```powershell
..\.venv\Scripts\python.exe -X utf8 -B evaluation/run_eval.py --pilot --replay evaluation/examples/synthetic_responses.jsonl --label synthetic_demo
```

## 真实复测

先按根目录README配置模型与高德，并启动本地后端。以下命令会联网调用服务并使用相应额度：

```powershell
..\.venv\Scripts\python.exe -X utf8 -B evaluation/run_eval.py --case-id dev_transport_beijing_elderly --apply-expected-walking-limit --live --label v2_local --model-label deepseek-flash --repeats 1
```

--apply-expected-walking-limit显式将题目原有步行约束映射为新增请求字段，并记录适配；不修改题目或评分标准。与V0相比HTTP输入字段有变化，不能称为完全相同输入的实验。模型标签由运行者填写，不证明后端实际配置。示例固定日期是历史日期，不应直接用于长期真实测试。

使用--pilot --live运行5条开发试测；默认逐条执行。留出集应在方案确定后评估，避免根据其结果反复调优。超时仅停止客户端等待，不保证撤销已发出的后端或模型调用。

## 结果与限制

每次运行新建results/标签_UTC时间_随机标识/，含run.json、records.jsonl、summary.json、report.md、manual_review.csv。results被Git忽略，本发布目录未携带本机历史原始记录。

逐项结果为passed、failed、unknown、not_applicable；缺少证据不能算通过。所有适用硬约束通过才标记整体passed，有失败为failed，没有失败但有未知为unknown。错误和超时保留在运行分母中。

当前HTTP模式未采集同次候选快照，来源检查通常unknown；补充坐标也需要同次详情证据。回放输入支持candidate_evidence，但文件本身不能证明来源真实，需采集链路保证。人工偏好、节奏、一致性按rubric填写，目前脚本不汇总人工表。token、实际费用、完整工具和模型调用量尚未采集。

截至2026-11-04的公开观测结果见[路线优化记录](../../docs/route-optimization.md)。其中V2老人单条相邻路线覆盖2/2、步行708/972米，自动结论unknown、系统审核needs_revision；它不是整个数据集的成功率。

# 旅游助手评测工具

包含20条合成评测草稿：10条开发、10条留出，普通规划、数量、交通、偏好各5条，其中5条开发样例标记为pilot。这些不是用户日志或已人工确认的标准答案，完整人工审阅与留出集评测尚未完成。

V7新增独立的`dining_preference_handling`：核对逐项偏好、同次POI类别/名称证据、等级、原严格要求和未知口味的标记。评分器不调用生产偏好分类器；菜系supported必须有对应类别，名称只能作为hint，清淡/饮食限制不能从POI判为已确认。缺少同次来源为unknown，旧回答没有偏好字段时为not_evaluated。该指标不改变原hard_status，也不代表实际菜单适配；本地菜类别映射仅配置成都→川菜。新增7项检查测试，总计54项评测工具测试通过。成都真实单条结果见[餐饮偏好记录](../../docs/dining-preferences.md)。

V7随后冻结源码做原五条开发pilot真实回归，各1次：交付5/5、原硬约束4passed/1failed、餐饮22/22，来源与三个新增说明/证据指标5/5通过。只有成都有具体餐饮偏好，本次3家类别支持、1家名称线索，清淡4家仍unknown。老人0/2合格公交路线，原1000米保留并说明原因/待确认选择。系统审核1passed/4needs_revision，可能误报与实际未知分开记录，人工表仍pending。详见[本轮完整结果](../../docs/full-regression-v7-20261105.md)。本轮不重复执行同源码已通过的单元/浏览器测试，没有新页面真实验收，不是20条全量或留出集。

V6新增独立的`dining_resolution_handling`：核对时段覆盖、当前首/末景点锚点、2000米直线半径与距离字段、暂定状态、原偏好及失败原因/待确认建议。它不参与原`hard_status`，不证明菜单、清淡做法、营业或实际接驳已经确认。新增5项评测工具测试，总计47项通过；历史回答没有该说明时仍记`not_evaluated`。

V5将`route_resolution_handling`作为独立的交付说明指标：检查原条件快照、路线覆盖、未完成原因、距离依据及替代建议的确认/未核验标记。它不参与原`hard_status`计算；解释清楚不代表缺失路线符合要求。旧回答缺少该字段时记`not_evaluated`，不事后补成通过。现有32项工具测试与新增10项检查/回放测试合计42项通过。

2026-11-05更新：V3 HTTP运行可保存后端candidate_evidence，包含同一request_id的景点/酒店/餐馆候选、工具搜索快照与详情补全记录。新版评分核对请求标识、来源成员、名称地址和有依据的坐标；无证据仍unknown，矛盾仍failed。旧记录使用原有规则，不补造来源。来源从unknown变为可核验属于观测能力改善，不能单独宣称模型质量提升。

cases.jsonl保留2026-11-04数据版本，出行日期从2026-11-05开始。长期使用前应建立新的日期版本，保留旧版本和运行记录；不要在两个对照版本之间偷偷修改题目。manifest部分notes是建立时的历史状态。

## 文件

| 文件 | 用途 |
| --- | --- |
| cases.jsonl、manifest.json | 题目、评分约定、版本和划分 |
| validate_cases.py | 离线检查20条题目和接口兼容性 |
| evaluators.py | 独立检查日期、数量、路线、步行、地点来源和预算 |
| run_eval.py | 预览、回放或显式运行真实后端，保存独立结果目录 |
| rubric.md | 评分标准与人工评分说明 |
| test_evaluation.py | 32项离线评测工具测试 |
| route_resolution_checks.py、test_route_resolution.py | 路线交付说明的独立检查和10项新增测试 |
| dining_checks.py、test_dining_checks.py | 餐饮候选交付的独立检查和5项新增测试 |
| dining_preference_checks.py、test_dining_preferences.py | 同次来源支持的逐项偏好检查和7项测试，不证明真实菜单适配 |
| replay_route_resolution.py | 对历史已交付结果离线重建说明，核对原请求、路线事实和原评分不变；不调用外部API |
| examples/synthetic_responses.jsonl | 合成回答，仅用于验证评分工具，不是真实Agent成绩 |

## 离线验证

以下命令从项目根目录进入backend后运行，假设虚拟环境位于项目根目录的.venv。普通校验和测试不调用模型或地图。

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -X utf8 -B evaluation/validate_cases.py
..\.venv\Scripts\python.exe -X utf8 -B -m unittest evaluation.test_evaluation
..\.venv\Scripts\python.exe -X utf8 -B -m unittest evaluation.test_evaluation evaluation.test_route_resolution
..\.venv\Scripts\python.exe -X utf8 -B -m unittest evaluation.test_evaluation evaluation.test_route_resolution evaluation.test_dining_checks
..\.venv\Scripts\python.exe -X utf8 -B -m unittest evaluation.test_evaluation evaluation.test_route_resolution evaluation.test_dining_checks evaluation.test_dining_preferences
..\.venv\Scripts\python.exe -X utf8 -B evaluation/run_eval.py --pilot
```

最后一个命令仅预览，不请求后端、不创建结果。macOS/Linux将Python路径替换为../.venv/bin/python。

用合成回答演示评分：

```powershell
..\.venv\Scripts\python.exe -X utf8 -B evaluation/run_eval.py --pilot --replay evaluation/examples/synthetic_responses.jsonl --label synthetic_demo
```

## 真实复测

如只验证新的说明逻辑，可先离线处理历史结果，不把它计为新live：

```powershell
..\.venv\Scripts\python.exe -X utf8 -B -m evaluation.replay_route_resolution --source-run evaluation/results/某个已完成批次 --label route_choices_replay
```

此命令只改变派生副本中的`route_resolution`及必要的程序审核摘要，保留原文件；请求、路线、景点、来源和原硬约束评分必须不变。新输出的HTTP状态和耗时为空，原始数值另存为`original_http_status`和`original_elapsed_s`，历史阶段耗时不代表本次执行。完整质量验证仍需真实请求及独立人工评价。

先按根目录README配置模型与高德，并启动本地后端。以下命令会联网调用服务并使用相应额度：

后端冷启动后先确认`http://127.0.0.1:8000/api/health`返回HTTP 200且`status`为`ok`，再运行live。若服务尚未就绪，连接失败仍会保留在该批次分母；不要删除旧错误或用重跑结果替换旧行，应另建批次并说明执行条件。

```powershell
..\.venv\Scripts\python.exe -X utf8 -B evaluation/run_eval.py --case-id dev_transport_beijing_elderly --apply-expected-walking-limit --live --label v2_local --model-label deepseek-flash --repeats 1
```

--apply-expected-walking-limit显式将题目原有步行约束映射为新增请求字段，并记录适配；不修改题目或评分标准。与V0相比HTTP输入字段有变化，不能称为完全相同输入的实验。模型标签由运行者填写，不证明后端实际配置。示例固定日期是历史日期，不应直接用于长期真实测试。

使用--pilot --live运行5条开发试测；默认逐条执行。留出集应在方案确定后评估，避免根据其结果反复调优。超时仅停止客户端等待，不保证撤销已发出的后端或模型调用。

## 结果与限制

V9用同一`--fixtures`数据检查数量保护后的目标检测与建议：直接执行建议须保留明确每日数量，改变固定数量的其他方向由程序标为未采用待确认，自动修改不使用。另可从backend运行`python experiments/revision_count_probe.py --output-root evaluation/results`预览固定初始修改联调，加`--live`才调用真实审核和规划模型，最多两轮修改；地理候选与路线全部模拟、没有新高德查询，不能称真实旅行端到端。最终联调保留2景点，将已知430分钟降至330分钟；拒绝错误数量草稿和保留旧行程分支另由固定故障测试覆盖。完整测试记录见[数量保护](../../docs/count-constraints-v9-20261105.md)。

不依赖本机私有旧记录的新四对合成样例，可从backend预览：

```powershell
python -X utf8 -B experiments/build_review_generalization.py
python -X utf8 -B experiments/review_probes.py --fixtures experiments/review_generalization_cases.json --output-root evaluation/results --label review_generalization_v8 --repeats 2
```

第二条不加`--live`只预览8例/16次计划；显式加`--live`才真实调用模型，不查询地图。预期标签在调用前固定，模型不接收标签；每对保持请求相同，仅改变待检因素。V8-02冻结生产代码执行16次，正常8次无目标误报，有错8次识别，但两次超时建议有减少景点而未明确待用户确认的风险，目标检测和建议质量分开记录。不能当独立人工或总体质量，详见[新组合结果](../../docs/review-generalization-v8-20261105.md)。

V8在同一组固定审核输入上验证名称/时间程序兜底。可在下述命令增加`--label review_guards_v8 --live --repeats 2`生成独立批次；脚本统计semantic_status，避免把语义调用失败但程序保留问题误当模型已完成。最终12次全部语义完成，名称冲突2/2拦截、正常对照4/4通过、雨天冲突2/2识别，18条程序时间核算一致。中间12次保留且有杭州疑似误报，最终北京仍有节奏意见；不是新整趟五条回归，详见[修复验证](../../docs/review-guards-v8-20261105.md)。原hard_status与54项评测工具没有因该审核修复放宽。

另提供`backend/experiments/review_probes.py`单独探测审核可靠性，固定两个已保存行程及两对正常/有错合成输入，不重新规划或调用地图。从backend运行`python experiments/review_probes.py --source evaluation/results/某个完整五条批次 --output-root evaluation/results`仅预览；显式加`--live --repeats 2`才进行12次真实审核。原最终行程的旧review会清除，避免泄漏；合成预期调用前固定、错误保留、失败不自动重试。标签由开发助手指定和复核，不能冒充独立人工；原样例可有其他真实问题，不能只按整体passed认定目标误报。V7本次未复现旧疑似误报，但发现名称矛盾漏报及摘要算术错误，详见[探测结果](../../docs/review-probes-v7-20261105.md)。该测试不改变原五条评分或既有54项工具测试计数。

每次运行新建results/标签_UTC时间_随机标识/，含run.json、records.jsonl、summary.json、report.md、manual_review.csv。results被Git忽略，本发布目录未携带本机历史原始记录。

逐项结果为passed、failed、unknown、not_applicable；缺少证据不能算通过。所有适用硬约束通过才标记整体passed，有失败为failed，没有失败但有未知为unknown。错误和超时保留在运行分母中。

V0/V1/V2历史HTTP记录未采集同次候选快照，来源检查unknown；V3由工具层采集并通过HTTP返回，评分器也核对详情补全坐标。回放支持candidate_evidence，但文件本身不能证明来源真实，采集链路仍需可信。人工偏好、节奏、一致性按rubric填写，目前脚本不汇总人工表。token、实际费用、完整工具和模型调用量尚未采集；stage_timings仅记录已完成阶段及审核等待耗时。

截至2026-11-04的公开观测结果见[路线优化记录](../../docs/route-optimization.md)。其中V2老人单条相邻路线覆盖2/2、步行708/972米，自动结论unknown、系统审核needs_revision；它不是整个数据集的成功率。

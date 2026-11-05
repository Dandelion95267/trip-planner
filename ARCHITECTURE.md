# 项目结构与运行流程

这是使用 HelloAgents、FastAPI 和 Vue 编写的本地旅游助手。模型负责生成和审核建议，Python代码负责查询资料、校验、组织流程、计算路线和预算。

## 一次规划请求怎样完成

```text
用户填写城市、日期、偏好和可选费用标准
                  ↓
Vue表单 → POST /api/trip/plan → FastAPI
                  ↓
TripPlanningWorkflow组织流程
                  ↓
AmapService通过高德MCP查询景点、天气和酒店候选
                  ↓
TripPlannerAgent使用SimpleAgent生成行程JSON
                  ↓
Pydantic解析 + 地点来源校验 + 日期校验
                  ↓
可选景点顺序优化 → 查询相邻景点实际路线
                  ↓
围绕当前各天景点查询餐馆 → 未完成时逐时段说明，路线未完成则标为暂定候选
                  ↓
路线是否完整核验？ → 否：保留原条件与已验证部分，返回原因和待用户选择的建议
                  ↓ 是
开启审核？ → TripReviewAgent返回问题和建议
                  ↓
需要修改？ → 复用候选资料修改、重新校验和查路线
            最多修改两轮，审核最多三次
                  ↓
根据最终行程和用户费用标准计算预算
                  ↓
返回TripPlan → 页面、地图、复制、JSON和PDF
```

关闭审核时跳过模型审核与修改步骤，仍生成程序路线核验说明；不填写费用标准时不计算预算。路线未完整核验时不再仅为该问题自动重写两轮，返回route_resolution.status=needs_choice，开启审核的请求记录needs_revision并说明其余安排尚未完成审核。用户通过原表单明确调整条件后发起新请求，替代建议本身不会应用。路线完整时，偏好、强度与一致性审核仍按原流程最多修改两轮。首次规划失败则返回错误提示。详见[路线待选择](docs/route-choice.md)。

V8审核增加`review_checks.py`：按当前相邻景点ID匹配路线，以整数秒计算游览及已知交通时间，公开`summary`由程序生成，时间独立放入`time_evidence`。缺失路线只统计已知部分，完整相邻路线也不代表包含用餐、休息、酒店/餐馆接驳。餐馆命名核对只解析有限明确格式，已知同ID别名可匹配、不同ID可定位冲突，简称/相似名/备选/条件等保留unknown；明确文本名称冲突附原文和字段依据，模型不能放行覆盖。程序问题和模型意见分开标source；语义调用失败时保留已知程序问题、semantic_status=unavailable，没有程序问题的调用失败仍不可用。待路线/餐饮选择分支附时间、标语义未运行。页面和导出使用同一程序数据。模型仍可能误判未知接驳或节奏，规则不保证覆盖所有自由文本；详见[审核程序核对及证据](docs/review-guards-v8-20261105.md)。

V9的`count_constraints.py`只从用户明确的每日数量表达识别上下界；模糊、条件、分日或冲突要求不猜测。审核建议保持原范围，固定数量的改变方向由后端分离为`alternative`（requires_confirmation=true、applied=false）；缩短游览时间与否定减少数量不混同。自动修改反馈剥离这些提案，向规划模型提供`revision_constraints`。真实规划器在修改草稿通过基本校验后、新路线查询前检查数量，工作流接纳修改前再检查；不符合时保留上一版及原审核结果、revisions不增加，并说明未采用原因。无明确数量不锁死旧个数，区间内改变允许。页面、复制、JSON和PDF保留待确认标记，具体边界和测试见[数量保护记录](docs/count-constraints-v9-20261105.md)。

## 后端代码在哪里

| 文件 | 负责什么 |
| --- | --- |
| `backend/app/config.py` | 从环境变量及后端`.env`读取配置，Key使用`SecretStr`保存 |
| `backend/app/services/llm_service.py` | 创建并复用`HelloAgentsLLM`客户端 |
| `backend/app/models/schemas.py` | 定义请求、地点、行程、路线、审核、预算等数据类和校验规则 |
| `backend/app/api/main.py` | HTTP接口入口，转换错误状态，最后计算预算；可提供构建后的网页 |
| `backend/app/services/trip_workflow.py` | 协调规划、审核和有限次数的修改，控制额外等待预算 |
| `backend/app/agents/trip_planner_agent.py` | 收集候选资料，生成或修改行程，校验来源并补充路线 |
| `backend/app/agents/review_agent.py` | 根据需求、已有资料和交通耗时审核，不直接修改行程 |
| `backend/app/services/amap_service.py` | 使用MCP SDK连接高德远程服务，解析地点、天气和路线 |
| `backend/app/services/route_policy.py` | 统一交通模式与路线步行距离口径，供输入、查询、规划与审核复用 |
| `backend/app/services/route_resolution.py` | 按原要求整理核验覆盖、未完成原因与待用户选择的调整建议，不额外调用API |
| `backend/app/services/route_search.py` | 同次请求的路线结果、诊断与只沿已验证有向边的有限路径搜索 |
| `backend/app/services/source_evidence.py` | 在地图结果解析后采集同请求搜索与详情，保存候选来源；不从模型答案反推 |
| `backend/app/services/dining_service.py` | 最终景点形成后周边搜餐馆，坐标粗筛，更新文字并保留失败提示 |
| `backend/app/services/itinerary_optimizer.py` | 用户开启后，根据直线距离减少景点折返，不保证道路最短 |
| `backend/app/services/budget_service.py` | 根据人数、房间数、天数和最终景点次数计算费用估算 |

`prepare_context()`收集的候选资料仅在一次请求内复用，修改时不会重新搜索一遍候选列表。每次调用模型会创建独立`SimpleAgent`，不共享对话历史。同步模型调用在线程池中执行，HTTP接口使用异步函数。

相邻景点路线缺失或不符合所选交通要求时，规划器在共享60秒路线预算内有限修复（每次生成最多24次额外候选边探测、每天最多12次），不再以设置步行上限为前提。设置`max_leg_walking_m`时，另外要求步行距离已知且不超限。先小范围替换，仍不可行时从候选中寻找整组可验证路线，保留当天景点数，不引入其他天已用的新景点。默认可替换首站；`preserve_first_attraction: true`约束程序和模型修改保留每日首站，开启顺序优化时也保持其固定首站约定。替换景点后清除不再对应当前景点的顺序距离对比。它不是预约或任意必游景点锁定功能。

V4.1先在预算内查询全部原有相邻路段，给每条分配查询时间，之后按剩余日期数分配修复时间，避免第一天耗尽整趟预算。两景点且未锁首站时，局部起点已累计多次明确失败后让出探测机会，交替尝试原第二站作起点与全候选组合。没有延长60秒总预算或降低1000米等用户上限；仍可能在预算内找不到解。

`TripPlanningContext.route_search`跨本次请求的修改轮次保存成功边和明确失败，临时失败最多实际尝试两次；模型也收到已查结果。`route_diagnostics`由程序记录状态、缓存命中和步行数据，不由模型生成。仍无法获取合格路线时明确保留未满足提示。审核者先由代码检查硬约束，失败直接返回修改反馈，不需要模型为数值规则判分。V2老人真实样例两段步行708、972米、路线覆盖2/2，但餐饮一致性审核仍未通过，耗时149秒，来源证据仍unknown。详见[优化说明与完整限制](docs/route-optimization.md)。

## 前端代码在哪里

V3的AmapService带请求级来源采集器：初次不再全城预选餐馆；generate_plan先完成模型、顺序和路线，再调用DiningService。V4曾在路线未完成时返回routes_pending并暂停整趟餐饮；V6取消该门控，围绕每一天当前首/末景点独立查询午餐/晚餐，并在该天交通未核验完整时标注暂定候选。失败只影响对应时段，已选餐馆保留；锚点ID/坐标改变后不沿用旧餐馆，预算不足则说明并给出待确认做法。使用maps_around_search在2000米直线半径筛选，直线距离不代表实际步行，菜单与饮食适配仍未核实。每次生成餐饮阶段35秒、每请求最多8次搜索和16次新详情；已有详情与相同锚点复用缓存。每日dining_resolution由后端生成，模型不能伪造，不进入原路线硬约束通过率。缺少来源采集器的旧自定义地图适配器保留旧接口行为。详见[餐饮独立查询](docs/independent-dining.md)。

V7的`dining_preferences.py`用有限规则拆分菜系、口味与严格饮食要求，`DiningService`在候选坐标与类别详情补全后过滤明确冲突，按类别依据、名称线索、距离排序。原关键词缺少可用类别依据时最多补充一次保留菜系方向的查询，沿用共享预算；不自动放宽严格要求。自动“本地菜”映射仅配置成都→川菜，其他城市保留未知。每条Meal带程序生成的逐项证据，类别支持方向、店名仅为线索，清淡/饮食限制不从POI推断已满足。规划模型输出会剥离这些字段，审核模型不能靠改写制造菜单证据。餐饮时段未补齐时流程交付具体原因及待确认做法；软口味未知已说明且有候选时仍审核其他真实问题。独立`dining_preference_handling`核对同次来源，不参与旧硬约束评分。页面、复制、JSON和PDF保留证据与确认选项。详见[餐饮偏好与真实验证](docs/dining-preferences.md)。

candidate_evidence、request_id、stage_timings和dining_diagnostics由后端填写，模型返回的同名字段被剔除。这些大体积诊断不进入后续模型提示。工具快照与分组保持原字段，坐标补全另记详情；独立评分器核对实际资料，旧记录不回填。阶段耗时不等于完整调用量或费用。

| 文件 | 负责什么 |
| --- | --- |
| `frontend/src/views/PlannerView.vue` | 表单、请求、等待、错误和停止等待 |
| `frontend/src/services/api.ts` | 使用`fetch`访问同源API，设置等待和取消处理 |
| `frontend/src/types/trip.ts` | TypeScript数据类型，与后端返回结构对应 |
| `frontend/src/components/TripResults.vue` | 日期切换、行程、吃住、审核状态与导出按钮 |
| `frontend/src/components/TripMap.vue` | 景点地图、可选餐饮住宿标记及坐标查询 |
| `frontend/src/components/BudgetSummary.vue` | 费用明细、计算依据、未知金额和估算范围 |
| `frontend/src/utils/trip_document.ts` | 将同一份最终行程整理为完整文档，供复制和PDF共用 |
| `frontend/src/services/pdf_export.ts` | 中文绘制、A4分页、页码和PDF生成；点击导出时加载 |

地图使用Leaflet和OpenStreetMap底图，高德坐标转换为WGS-84后展示。虚线表示游览顺序；实际交通方式和耗时请看路线卡片。

## 三种导出怎样保持一致

- JSON保存接口返回的完整`TripPlan`。
- 复制和PDF共用`buildTripDocument()`，包含全部日期、景点、天气、路线、餐饮、住宿、审核、预算与建议。
- 导出不重新调用模型和高德。PDF文字以图像保存，不能选取搜索，不包含地图截图。

## 如何启动与检查

本机运行、配置和测试命令见[README](README.md)。测试脚本和实际验收结论见[验收记录](ACCEPTANCE.md)。

修改数据字段时同步检查`schemas.py`、`types/trip.ts`、表单和结果展示；修改费用规则时先看`budget_service.py`；修改工具查询时先看`amap_service.py`。

## 当前边界

这是可本地运行的学习项目，没有账户、数据库持久化、预订或支付功能。候选酒店和餐馆不代表已确认房态、菜单或营业情况。天气只使用查询到的预报日期；开放时间和预约需要另行核实。审核是模型判断，可能遗漏问题，达到修改上限时仍会保留问题提示。

预算是用户标准的估算，不是实际报价或价格上限，不含往返目的地的大交通等费用。“停止等待”停止浏览器等待，不保证撤销已经发送的后端或模型请求。

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
AmapService通过高德MCP查询景点、天气、酒店和餐馆
                  ↓
TripPlannerAgent使用SimpleAgent生成行程JSON
                  ↓
Pydantic解析 + 地点来源校验 + 日期校验
                  ↓
可选景点顺序优化 → 查询相邻景点实际路线
                  ↓
开启审核？ → TripReviewAgent返回问题和建议
                  ↓
需要修改？ → 复用候选资料修改、重新校验和查路线
            最多修改两轮，审核最多三次
                  ↓
根据最终行程和用户费用标准计算预算
                  ↓
返回TripPlan → 页面、地图、复制、JSON和PDF
```

关闭审核时跳过审核与修改步骤；不填写费用标准时不计算预算。审核或修改失败会返回最后一次有效行程，并明确说明审核状态。首次规划失败则返回错误提示。

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
| `backend/app/services/itinerary_optimizer.py` | 用户开启后，根据直线距离减少景点折返，不保证道路最短 |
| `backend/app/services/budget_service.py` | 根据人数、房间数、天数和最终景点次数计算费用估算 |

`prepare_context()`收集的候选资料仅在一次请求内复用，修改时不会重新搜索一遍候选列表。每次调用模型会创建独立`SimpleAgent`，不共享对话历史。同步模型调用在线程池中执行，HTTP接口使用异步函数。

## 前端代码在哪里

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

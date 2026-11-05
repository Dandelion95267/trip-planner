# V4.1多天路线预算修复：北京定向复测

日期：2026-11-05。原题目、请求和评分规则保留，业务代码在本批运行中冻结。

批次：v41_fair_route_target_20261105_20261105T043815Z_f161a487
源码SHA256：00ffca5ccec0f519543e0e98d09f96316e990e50869f62d12445e8c3c9da8476

## 问题与修改

原来只有max_leg_walking_m不为空时才修复景点路线、复用跨轮状态并反馈已查结果；普通公交请求查不到时只提示。审核入口同样只对有上限的请求做程序检查。
现在支持的交通方式都先检查并有限修复，保留当天景点数、交通方式和首站约束。未设置步行上限时不擅自增加上限。

| 文件 | 本轮作用 |
| --- | --- |
| trip_planner_agent.py | 所有受支持请求复用路线状态、反馈失败并有限替换；替换后清除旧顺序对比 |
| route_policy.py | 统一路线缺失、模式和可选步行上限检查 |
| review_agent.py | 程序检查未通过时，先进入原有有限修改流程 |
| dining_service.py | 必要路线未稳定时保留餐馆查询预算 |
| test_route_repair.py及原测试 | 总计156项后端与32项评测工具测试通过 |

每次生成共享60秒路线预算，每天最多12次、合计24次额外候选边探测；临时失败跨轮最多实际尝试两次，工作流最多修改两轮。没有增加无限重试。

V4首批老人案例只有1/2段合格路线、餐饮0/4，三轮路线阶段均耗尽60秒。前一天先修复，后一天查询机会不足；局部探测也跨轮集中在同一起点。
V4.1先给全部原路段分配查询机会，再按剩余日期数分配修复时间；两景点未锁首站时分散探测原第二站与其他候选，累计多次失败后不再持续偏向原起点。新增3项固定测试。

## 本批真实结果

| 样例 | 自动检查 | 来源 | 路线 | 餐饮 | 系统审核 | 秒数 |
| --- | --- | --- | --- | --- | --- | --- |
| dev_transport_beijing_elderly | failed | passed | 0/2 | 0/4 | needs_revision | 211.9834 |

## 与上一完整批次对照

| 指标 | 修改前 | 本批 |
| --- | --- | --- |
| 交付 | 1/1 | 1/1 |
| 独立自动状态 | {'failed': 1} | {'failed': 1} |
| 来源状态 | {'passed': 1} | {'passed': 1} |
| 相邻路线 | 1/2 | 0/2 |
| 餐饮时段 | 0/4 | 0/4 |
| 系统审核 | {'needs_revision': 1} | {'needs_revision': 1} |
| 平均秒数 | 212.7773 | 211.9834 |

## 遗留项与助手核对

dev_transport_beijing_elderly独立检查：route_modes=unknown；Day 0 has a missing route; mode is unknown.；Day 1 has a missing route; mode is unknown.
dev_transport_beijing_elderly独立检查：route_completeness=failed；Required adjacent routes are missing or no required pair was generated.
dev_transport_beijing_elderly独立检查：walking_limit=unknown；Day 0 has a missing route; walking is unknown.；Day 1 has a missing route; walking is unknown.
dev_transport_beijing_elderly系统审核线索（非独立人工结论）：“天安门广场 → 太庙”未满足或验证路线要求。
dev_transport_beijing_elderly系统审核线索（非独立人工结论）：“景山公园 → 北海公园”未满足或验证路线要求。

餐馆坐标补充核对问题数：0，没有改写原评分。

单次开发样例测试；没有稳定重复或留出集验证。系统审核和助手补充核对不是独立人工质量评分。
修复有查询预算，不保证总能找到可行路线；失败仍保留未验证提示和needs_revision，不用降级交通或减少景点数伪装通过。
未设数值上限的公交方案可能含较长接驳步行；不能把路线存在等同于适合老人或出行轻松。
餐馆菜系、清淡做法、菜单、营业、酒店接驳及预约尚未核实；直线距离不代表真实步行距离。
增加查询与审核可能增加延迟；token、实际费用与完整调用量未采集，阶段耗时不能替代费用。

## 证据与可比性

对照基线：v4_route_pilot_20261105_20261105T042234Z_8425f680
题目SHA256：effd5efbab62675390d4eacf102c281f251bc2a03d6d63cc3491428b4b2a0ce5
相同case_id的请求逐字段相同；外部模型和地图响应没有冻结，每条仅一次，延迟或通过数量变化不是稳定因果结论。
部分题目没有固定每日景点数，模型安排可变化；路线覆盖分母按各次最终景点计算，不能把分母变化当作相同路线集合的修复率。exact2及max2仍沿用原数量检查。
定向批次与完整五条分开保存，不以定向成功结果替换整批某条。人工评分CSV保持未填写。

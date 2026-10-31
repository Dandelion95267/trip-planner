# 项目来源与许可说明

本项目是基于 Datawhale [Hello-Agents](https://github.com/datawhalechina/hello-agents) 教程第十三章进行的学习实践和二次开发，使用 HelloAgents 框架调用模型。它不是 Hello-Agents 官方发布的产品。

参考来源：

- [Hello-Agents 教程](https://github.com/datawhalechina/hello-agents)。
- [第十三章配套项目](https://github.com/datawhalechina/hello-agents/tree/main/code/chapter13/helloagents-trip-planner)。

本版本重新组织了服务查询和规划流程，并加入或调整了独立审核、有限次数的修改、地点来源校验、路线比较、可选顺序优化、费用估算、复制/PDF导出和自动化测试。主要流程为一个规划 Agent、一个审核 Agent与确定性代码协作，具体结构见`ARCHITECTURE.md`。

本仓库保留上游教程的 CC BY-NC-SA 4.0 许可全文，见[LICENSE](LICENSE)。本项目对教程内容的改编及新增贡献采用相同许可发布；转载时保留来源、许可及修改说明。该许可包含非商业性使用条件，不能据此假定已经获得商业运营许可。

HelloAgents、FastAPI、Vue、jsPDF等第三方依赖仍受其各自许可证约束，本文件不替代这些许可证。上游作者和第三方服务商不因此为本项目背书。

截图与示例PDF来自模拟测试数据；地图界面保留OpenStreetMap署名，截图底图是测试瓦片。示例金额为手动设定的测算标准，不是实际价格或出行建议。

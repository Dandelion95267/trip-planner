"""组织规划与审核，最多修改两轮；失败时保留最后一次校验通过的行程。"""

import asyncio
from time import perf_counter
from typing import Optional

from app.agents.review_agent import TripReviewAgent, TripReviewError
from app.agents.trip_planner_agent import TripPlannerAgent, TripPlanningContext, TripPlanningError, TripCountConstraintError
from app.models.schemas import StageTiming, TripPlan, TripRequest, TripReviewIssue, TripReviewResult, TripReviewSummary
from app.services.amap_service import AmapServiceError
from app.services.route_resolution import build_route_resolution
from app.services.review_checks import build_time_evidence, check_meal_names
from app.services.count_constraints import count_issues


MAX_REVISIONS = 2
REVIEW_BUDGET_SECONDS = 180


class TripPlanningWorkflow:
    """一次请求独立运行，不共享行程、审核记录或候选资料。"""

    def __init__(self, planner: TripPlannerAgent, reviewer: Optional[TripReviewAgent] = None):
        self.planner = planner
        self.reviewer = reviewer

    async def plan_trip(self, request: TripRequest) -> TripPlan:
        if not request.enable_review:
            plan = await self.planner.plan_trip(request)
            self._await_route_choice(request, plan)
            return plan

        context = await self.planner.prepare_context(request)
        plan = await self.planner.generate_plan(request, context)
        if self._await_route_choice(request, plan) or self._await_dining_choice(request, plan):
            return plan
        reviewer = self.reviewer if self.reviewer is not None else TripReviewAgent(llm=self.planner.llm)
        deadline = asyncio.get_running_loop().time() + REVIEW_BUDGET_SECONDS

        for revision in range(MAX_REVISIONS + 1):
            started = perf_counter()
            try:
                remaining = max(0, deadline - asyncio.get_running_loop().time())
                result = await asyncio.wait_for(reviewer.review_trip(request, plan), timeout=remaining)
            except (TripReviewError, TimeoutError):
                plan.review = TripReviewSummary(
                    status="unavailable", review_count=revision + 1, revisions=revision,
                    message="审核未能完成，保留当前有效行程；本次不能视为审核通过。",
                )
                return plan
            finally:
                if isinstance(context, TripPlanningContext):
                    context.timings.append(StageTiming(generation=context.generation, stage="review", elapsed_s=round(perf_counter() - started, 4)))
                    self.planner.sync_metadata(plan, context)

            if result.passed or revision == MAX_REVISIONS:
                plan.review = TripReviewSummary(
                    status="passed" if result.passed else "needs_revision",
                    review_count=revision + 1, revisions=revision, result=result,
                    message=("审核未发现有明确依据的待修改问题，出行前仍需确认实际条件。" if result.passed
                             else "已达到两轮修改上限，仍有待确认的问题，请查看审核意见。"),
                )
                return plan

            try:
                remaining = max(0, deadline - asyncio.get_running_loop().time())
                executable_feedback = result.model_copy(deep=True)
                for issue in executable_feedback.issues:
                    issue.alternative = None
                updated_plan = await asyncio.wait_for(
                    self.planner.generate_plan(
                        request, context, previous_plan=plan.model_copy(deep=True),
                        review_feedback=executable_feedback,
                    ), timeout=remaining,
                )
                violations = count_issues(request, updated_plan)
                if violations:
                    raise TripCountConstraintError("；".join(issue.evidence for issue in violations))
            except TripCountConstraintError as error:
                plan.review = TripReviewSummary(
                    status="needs_revision", review_count=revision + 1, revisions=revision, result=result,
                    message=f"本次修改未通过原每日数量校验（{error}），修改未采用，保留上一版行程及其审核意见。若愿意改变数量，请先修改需求后重新规划。",
                )
                return plan
            except (TripPlanningError, AmapServiceError, TimeoutError):
                plan.review = TripReviewSummary(
                    status="needs_revision", review_count=revision + 1, revisions=revision, result=result,
                    message="调整未能完成，保留上一次有效行程，请查看尚未解决的审核意见。",
                )
                return plan
            plan = updated_plan
            if (self._await_route_choice(request, plan, review_count=revision + 1, revisions=revision + 1)
                    or self._await_dining_choice(request, plan, review_count=revision + 1, revisions=revision + 1)):
                return plan

        raise RuntimeError("审核流程未能正常结束。")

    @staticmethod
    def _await_route_choice(request: TripRequest, plan: TripPlan, review_count: int = 0, revisions: int = 0) -> bool:
        """有限路线搜索未完成时等待用户选择，不为同一缺失路线强行重写两轮。"""
        resolution = build_route_resolution(request, plan)
        plan.route_resolution = resolution
        if resolution.status != "needs_choice":
            return False
        if request.enable_review:
            plan.review = TripReviewSummary(
                status="needs_revision", review_count=review_count, revisions=revisions,
                result=TripReviewResult(
                    passed=False, summary="部分路线尚未核验通过，已提供可选做法，等待你的选择。",
                    time_evidence=build_time_evidence(plan), meal_consistency=check_meal_names(plan)[0], semantic_status="not_run",
                    issues=[TripReviewIssue(
                        category="preference", day_index=x.day_index, source="program",
                        problem=f"“{x.origin_name} → {x.destination_name}”尚未按原要求核验通过。",
                        evidence=x.explanation,
                        suggestion="查看路线待选择区域；可保持原条件重试，或自行决定是否调整景点、步行上限或交通方式后重新规划。",
                    ) for x in resolution.unresolved] + count_issues(request, plan),
                ),
                message="本次有限路线搜索未完成全部核验，已保留原条件和已验证路线。请先选择下一步；其余行程安排尚未完成审核。",
            )
        return True

    @staticmethod
    def _await_dining_choice(request: TripRequest, plan: TripPlan, review_count: int = 0, revisions: int = 0) -> bool:
        unresolved = [(day, item) for day in plan.days if day.dining_resolution for item in day.dining_resolution.unresolved]
        if not unresolved:
            return False
        if request.enable_review:
            plan.review = TripReviewSummary(
                status="needs_revision", review_count=review_count, revisions=revisions,
                result=TripReviewResult(
                    passed=False, summary="部分用餐时段尚未取得符合筛选要求的候选，已提供原因和待确认做法。",
                    time_evidence=build_time_evidence(plan), meal_consistency=check_meal_names(plan)[0], semantic_status="not_run",
                    issues=[TripReviewIssue(
                        category="preference", day_index=day.day_index, source="program",
                        problem=f"{'午餐' if item.meal_type == 'lunch' else '晚餐'}（{item.anchor_name}附近）尚未安排可核验候选。",
                        evidence=item.explanation,
                        suggestion="保留原餐饮要求重查，或联系商家核实菜单；只有你愿意改变偏好时才修改需求后重新规划。",
                    ) for day, item in unresolved] + count_issues(request, plan),
                ),
                message="本次有用餐时段尚未完成，已保留原偏好和其他候选。请先查看餐饮说明并选择下一步；其余行程安排尚未完成审核。",
            )
        return True

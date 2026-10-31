"""组织规划与审核，最多修改两轮；失败时保留最后一次校验通过的行程。"""

import asyncio
from typing import Optional

from app.agents.review_agent import TripReviewAgent, TripReviewError
from app.agents.trip_planner_agent import TripPlannerAgent, TripPlanningError
from app.models.schemas import TripPlan, TripRequest, TripReviewSummary
from app.services.amap_service import AmapServiceError


MAX_REVISIONS = 2
REVIEW_BUDGET_SECONDS = 180


class TripPlanningWorkflow:
    """一次请求独立运行，不共享行程、审核记录或候选资料。"""

    def __init__(self, planner: TripPlannerAgent, reviewer: Optional[TripReviewAgent] = None):
        self.planner = planner
        self.reviewer = reviewer

    async def plan_trip(self, request: TripRequest) -> TripPlan:
        if not request.enable_review:
            return await self.planner.plan_trip(request)

        context = await self.planner.prepare_context(request)
        plan = await self.planner.generate_plan(request, context)
        reviewer = self.reviewer if self.reviewer is not None else TripReviewAgent(llm=self.planner.llm)
        deadline = asyncio.get_running_loop().time() + REVIEW_BUDGET_SECONDS

        for revision in range(MAX_REVISIONS + 1):
            try:
                remaining = max(0, deadline - asyncio.get_running_loop().time())
                result = await asyncio.wait_for(reviewer.review_trip(request, plan), timeout=remaining)
            except (TripReviewError, TimeoutError):
                plan.review = TripReviewSummary(
                    status="unavailable", review_count=revision + 1, revisions=revision,
                    message="审核未能完成，保留当前有效行程；本次不能视为审核通过。",
                )
                return plan

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
                updated_plan = await asyncio.wait_for(
                    self.planner.generate_plan(
                        request, context, previous_plan=plan.model_copy(deep=True),
                        review_feedback=result.model_copy(deep=True),
                    ), timeout=remaining,
                )
            except (TripPlanningError, AmapServiceError, TimeoutError):
                plan.review = TripReviewSummary(
                    status="needs_revision", review_count=revision + 1, revisions=revision, result=result,
                    message="调整未能完成，保留上一次有效行程，请查看尚未解决的审核意见。",
                )
                return plan
            plan = updated_plan

        raise RuntimeError("审核流程未能正常结束。")

"""数量要求、待确认提案和真实修改入口的故障回归，不调用外部服务。"""

import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.agents.review_agent import TripReviewAgent
from app.agents.trip_planner_agent import TripPlannerAgent, TripPlanningContext, TripCountConstraintError
from app.models.schemas import POIInfo, ReviewAlternative, TripReviewIssue
from app.services.count_constraints import count_issues, daily_count_rule, constrain_suggestions
from app.services.trip_workflow import TripPlanningWorkflow
from tests.test_review_checks import inputs
from tests.test_trip_workflow import make_inputs


class CountRuleTests(unittest.TestCase):
    def setUp(self):
        self.request, self.plan = inputs()

    def test_clear_exact_arabic_chinese_and_spacing(self):
        for text,n in [('每天两个景点',2),('每天恰好安排三个景点',3),('每日正好2个景点',2),('每一天安排 2 个景点',2),('每天需要安排二十个景点',20)]:
            with self.subTest(text=text):
                self.request.free_text_input=text
                rule=daily_count_rule(self.request)
                self.assertEqual((rule.status,rule.minimum,rule.maximum),('recognized',n,n))

    def test_lower_upper_and_range(self):
        for text,bounds in [('每天至少一个、最多两个景点',(1,2)),('每天至少一个景点，最多两个景点',(1,2)),('每天安排1至2个景点',(1,2)),('每天最多两个景点',(None,2)),('每天至少两个景点',(2,None))]:
            with self.subTest(text=text):
                self.request.free_text_input=text
                rule=daily_count_rule(self.request)
                self.assertEqual(rule.status,'recognized')
                self.assertEqual((rule.minimum,rule.maximum),bounds)

    def test_fuzzy_conditional_examples_and_unrelated_numbers_abstain(self):
        for text in ['两个人旅行三天','第一天两个景点，第二天三个景点','每天大概两个景点','每天两个景点左右','如果天气好，每天两个景点','每天两个景点，如果累了就减少','不要求每天两个景点','比如每天两个景点','每天两个或三个景点','每天两个景点，或者三个景点','每天下午最多两个景点']:
            with self.subTest(text=text):
                self.request.free_text_input=text
                self.assertNotEqual(daily_count_rule(self.request).status,'recognized')
                self.assertEqual(count_issues(self.request,self.plan),[])

    def test_conflicting_explicit_rules_are_ambiguous(self):
        self.request.free_text_input='每天两个景点。每天三个景点。'
        self.assertEqual(daily_count_rule(self.request).status,'ambiguous')
        self.assertEqual(count_issues(self.request,self.plan),[])

    def test_count_check_uses_user_rule_and_does_not_mutate_plan(self):
        self.request.free_text_input='每天恰好三个景点'
        before=self.plan.model_dump()
        issues=count_issues(self.request,self.plan)
        self.assertEqual(len(issues),1)
        self.assertEqual(issues[0].source,'program')
        self.assertIn('实际2个',issues[0].evidence)
        self.assertEqual(self.plan.model_dump(),before)

    def test_rule_range_allows_lower_number_within_bounds(self):
        self.request.free_text_input='每天至少一个、最多两个景点'
        self.plan.days[0].attractions.pop()
        self.assertEqual(count_issues(self.request,self.plan),[])

    def test_unsafe_exact_count_proposal_separated_from_executable_suggestion(self):
        self.request.free_text_input='每天安排两个景点'
        issue=TripReviewIssue(category='pace',problem='时间超限',evidence='430超过360分钟',suggestion='压缩游览时间或减少一个景点。')
        result=constrain_suggestions(self.request,[issue])[0]
        self.assertIn('恰好2个',result.suggestion)
        self.assertIsNotNone(result.alternative)
        self.assertEqual(result.alternative.proposal,issue.suggestion)
        self.assertFalse(result.alternative.applied)
        self.assertTrue(result.alternative.requires_confirmation)
        self.assertIsNone(issue.alternative)

    def test_shortening_duration_and_negative_count_change_are_not_misclassified(self):
        self.request.free_text_input='每天两个景点'
        for text in ['减少景点游览时长。','减少景点的游览时间。','不要减少景点，缩短停留时间。','保持数量不变，不减少景点。',
                     '不得通过减少景点数量来规避要求。','不要通过减少景点或编造交通时间来满足上限。','不能以减少景点数量来达标。']:
            with self.subTest(text=text):
                issue=TripReviewIssue(category='pace',problem='复核',evidence='模拟',suggestion=text)
                self.assertIsNone(constrain_suggestions(self.request,[issue])[0].alternative)


class CountWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request,self.plan,self.failed,self.passed=make_inputs()
        self.planner=Mock()
        self.planner.prepare_context=AsyncMock(return_value=object())
        self.reviewer=Mock()
        self.reviewer.review_trip=AsyncMock(side_effect=[self.failed,self.passed])
        self.workflow=TripPlanningWorkflow(self.planner,self.reviewer)

    async def test_rejected_count_change_keeps_previous_result_and_counters(self):
        self.request.free_text_input='每天恰好三个景点'
        invalid=self.plan.model_copy(deep=True)
        invalid.days[0].attractions.pop()
        self.planner.generate_plan=AsyncMock(side_effect=[self.plan,invalid])
        result=await self.workflow.plan_trip(self.request)
        self.assertIs(result,self.plan)
        self.assertEqual(len(result.days[0].attractions),3)
        self.assertEqual(result.review.revisions,0)
        self.assertEqual(result.review.review_count,1)
        self.assertIn('修改未采用',result.review.message)
        self.assertIn('实际2个',result.review.message)
        self.assertEqual(result.review.result,self.failed)
        self.assertEqual(self.reviewer.review_trip.await_count,1)

    async def test_same_count_revision_is_accepted(self):
        self.request.free_text_input='每天三个景点'
        updated=self.plan.model_copy(deep=True)
        updated.days[0].attractions[0].visit_duration=60
        self.planner.generate_plan=AsyncMock(side_effect=[self.plan,updated])
        result=await self.workflow.plan_trip(self.request)
        self.assertIs(result,updated)
        self.assertEqual(result.review.revisions,1)
        self.assertEqual(result.review.status,'passed')

    async def test_no_explicit_count_does_not_lock_previous_counts(self):
        updated=self.plan.model_copy(deep=True)
        updated.days[0].attractions.pop()
        self.planner.generate_plan=AsyncMock(side_effect=[self.plan,updated])
        result=await self.workflow.plan_trip(self.request)
        self.assertEqual(len(result.days[0].attractions),2)
        self.assertEqual(result.review.status,'passed')

    async def test_pending_alternative_not_sent_to_automatic_modifier(self):
        self.request.free_text_input='每天三个景点'
        self.failed.issues[0].alternative=ReviewAlternative(proposal='减少景点，需用户决定')
        self.planner.generate_plan=AsyncMock(side_effect=[self.plan,self.plan.model_copy(deep=True)])
        await self.workflow.plan_trip(self.request)
        feedback=self.planner.generate_plan.await_args_list[1].kwargs['review_feedback']
        self.assertIsNone(feedback.issues[0].alternative)
        self.assertIsNotNone(self.failed.issues[0].alternative)


class RealEntryGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_count_violation_rejected_before_map_queries_and_alternative_excluded(self):
        request,plan,failed,_=make_inputs()
        request.free_text_input='每天三个景点'
        failed.issues[0].alternative=ReviewAlternative(proposal='删除一个景点，需确认')
        candidates=[POIInfo(id=a.poi_id,name=a.name,address=a.address) for a in plan.days[0].attractions]
        context=TripPlanningContext(candidates,[],None,[],None,[],None)
        maps=Mock()
        maps.plan_route=AsyncMock()
        planner=TripPlannerAgent(llm=Mock(),amap_service=maps)
        invalid=plan.model_copy(deep=True)
        invalid.days[0].attractions.pop()
        with patch('app.agents.trip_planner_agent.SimpleAgent') as agent:
            agent.return_value.run.return_value=invalid.model_dump_json()
            with self.assertRaises(TripCountConstraintError):
                await planner.generate_plan(request,context,previous_plan=plan,review_feedback=failed)
            payload=json.loads(agent.return_value.run.call_args.args[0])
            self.assertEqual(payload['revision_constraints']['minimum'],3)
            self.assertNotIn('alternative',payload['review_feedback']['issues'][0])
        maps.plan_route.assert_not_awaited()

    async def test_reviewer_separates_model_count_change_and_drops_forged_alternative(self):
        req,plan=inputs()
        req.free_text_input='每天两个景点'
        reviewer=TripReviewAgent(llm=Mock())
        with patch('app.agents.review_agent.SimpleAgent') as agent:
            agent.return_value.run.return_value=json.dumps({'passed':False,'summary':'超时','issues':[{'category':'pace','problem':'超时','evidence':'模拟依据','suggestion':'减少一个景点。','alternative':{'applied':True}}]})
            result=await reviewer.review_trip(req,plan)
        issue=result.issues[0]
        self.assertIn('恰好2个',issue.suggestion)
        self.assertIsNotNone(issue.alternative)
        self.assertFalse(issue.alternative.applied)

    async def test_initial_count_error_is_program_issue_not_model_pass(self):
        req,plan=inputs()
        req.free_text_input='每天三个景点'
        with patch('app.agents.review_agent.SimpleAgent') as agent:
            result=await TripReviewAgent(llm=Mock()).review_trip(req,plan)
        self.assertFalse(result.passed)
        self.assertEqual(result.semantic_status,'not_run')
        self.assertEqual(result.issues[0].source,'program')
        agent.assert_not_called()


if __name__=='__main__':
    unittest.main()

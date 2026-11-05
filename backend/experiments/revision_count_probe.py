"""固定超时初始行程，真实审核和修改联调；地图资料模拟，不是整趟真实规划。"""

import argparse
import asyncio
import contextlib
from datetime import datetime,timezone
import hashlib
import io
import json
import logging
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

BACKEND=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BACKEND))
from app.agents.review_agent import TripReviewAgent
from app.agents.trip_planner_agent import TripPlannerAgent,TripPlanningContext
from app.models.schemas import POIInfo,RoutePlan,TripPlan,TripRequest
from app.services.llm_service import get_llm
from app.services.trip_workflow import TripPlanningWorkflow
from evaluation.run_eval import local_source_hash


class FixedMaps:
    source_evidence=None
    def __init__(self,places):
        self.places={p.id:p for p in places}
        self.route_calls=0

    async def plan_route(self,query):
        self.route_calls+=1
        return RoutePlan(origin=self.places[query.origin_poi_id],destination=self.places[query.destination_poi_id],
            mode=query.mode,duration_s=1800,distance_m=500,walking_distance_m=500 if query.mode=='transit' else None)


class FixedInitialPlanner:
    def __init__(self,actual,context,initial):
        self.actual,self.context,self.initial=actual,context,initial
        self.llm=actual.llm
        self.revision_attempts=0

    async def prepare_context(self,request):
        return self.context

    async def generate_plan(self,request,context,previous_plan=None,review_feedback=None):
        if previous_plan is None:
            return self.initial.model_copy(deep=True)
        self.revision_attempts+=1
        return await self.actual.generate_plan(request,context,previous_plan=previous_plan,review_feedback=review_feedback)

    def sync_metadata(self,plan,context):
        self.actual.sync_metadata(plan,context)


async def run(args):
    fixture=json.loads(args.fixtures.read_text(encoding='utf-8'))
    case=next(c for c in fixture['cases'] if c['probe_id']=='pace_exceeds_explicit_budget')
    request=TripRequest.model_validate(case['request'])
    initial=TripPlan.model_validate(case['plan'])
    places=[POIInfo(id=a.poi_id,name=a.name,address=a.address) for a in initial.days[0].attractions]
    hotels=[POIInfo(id=initial.days[0].hotel.poi_id,name=initial.days[0].hotel.name,address=initial.days[0].hotel.address)]
    restaurants=[POIInfo(id=m.poi_id,name=m.name,address=m.address or '') for m in initial.days[0].meals]
    context=TripPlanningContext(places,[],None,hotels,None,restaurants,None,generation=1)
    maps=FixedMaps(places)
    if not args.live:
        print(json.dumps({'mode':'preview','initial_attractions':2,'initial_known_minutes':430,'requested_count':2,'requested_max_minutes':360,'max_revisions':2,'map_data':'simulated'},ensure_ascii=False))
        return
    folder=args.output_root/f'revision_count_v9_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}_{uuid4().hex[:8]}'
    folder.mkdir(parents=True,exist_ok=False)
    code_hash=local_source_hash()
    (folder/'inputs.json').write_text(json.dumps(case,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'run.json').write_text(json.dumps({'mode':'live_review_and_revision_fixed_initial','local_source_sha256':code_hash,
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'fixture_sha256':hashlib.sha256(args.fixtures.read_bytes()).hexdigest(),
        'initial_generation':'fixed_synthetic_input_not_live','review_and_revision':'real_model_calls',
        'map_calls_external':0,'map_data':'simulated','max_revisions':2,'token_cost':'not_collected'},ensure_ascii=False,indent=2),encoding='utf-8')
    logging.disable(logging.CRITICAL)
    start=perf_counter()
    record={'result':None,'error':None}
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
        llm=get_llm()
        planner=FixedInitialPlanner(TripPlannerAgent(llm=llm,amap_service=maps),context,initial)
        try:
            result=await TripPlanningWorkflow(planner,TripReviewAgent(llm=llm)).plan_trip(request)
            record['result']=result.model_dump(mode='json')
        except Exception as error:
            record['error']={'type':type(error).__name__,'message':'固定初始行程的审核修改联调失败，不能视为通过。'}
    record.update(elapsed_s=round(perf_counter()-start,4),revision_attempts=planner.revision_attempts,simulated_route_calls=maps.route_calls)
    if record['result']:
        plan=record['result']
        record['checks']={'daily_count_preserved':all(len(d['attractions'])==2 for d in plan['days']),
            'known_minutes':[sum(a['visit_duration'] for a in d['attractions'])+sum(r['duration_s'] for r in d['routes'])/60 for d in plan['days']],
            'review_status':plan['review']['status'],'accepted_revisions':plan['review']['revisions']}
    (folder/'record.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    assert local_source_hash()==code_hash
    print(json.dumps({'folder':str(folder),'checks':record.get('checks'),'error':record['error'],'elapsed_s':record['elapsed_s']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures',type=Path,default=Path(__file__).with_name('review_generalization_cases.json'))
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--live',action='store_true')
    asyncio.run(run(parser.parse_args()))

"""生成四对新审核样例，预先固定预期；不调用模型、地图或修改业务。"""

import json
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.models.schemas import CandidateEvidence, POIInfo, TripPlan, TripRequest, WeatherInfo


def make_base():
    req = TripRequest(city='测试城市', start_date='2026-11-05', end_date='2026-11-05', travel_days=1,
        transportation='步行', route_mode='walking', accommodation='经济型酒店', enable_review=True,
        free_text_input='每天至少1个、最多2个景点，兼顾休息。未知的营业、菜单、房态和接驳请明确提示，不得宣称已核实。')
    plan = TripPlan(city=req.city,start_date=req.start_date,end_date=req.end_date,
        overall_suggestions='以下为虚构地点、模拟路线和预报，只能根据给定资料审核；没有真实预订或商家确认。',
        days=[{'date':req.start_date,'day_index':0,'transportation':'步行','accommodation':'经济型酒店',
               'description':'上午游览水岸展馆，午餐候选：晴川饭馆。午餐后自由休息，下午参观工艺馆。菜单、营业、房态和酒店/餐馆实际接驳均未核验，直线距离不等于步行距离。',
               'attractions':[{'poi_id':pid,'name':name,'address':'模拟地址','visit_duration':45,'description':'室内展厅参观。'} for pid,name in [('mock_a','水岸展馆'),('mock_b','工艺馆')]],
               'meals':[{'poi_id':'mock_meal','type':'lunch','name':'晴川饭馆','anchor_poi_id':'mock_a','straight_line_distance_m':800,
                         'description':'到参考景点为模拟直线距离800米；实际接驳、菜单和营业均未核验。'}],
               'hotel':{'poi_id':'mock_hotel','name':'松风旅舍','address':'模拟酒店地址'},
               'routes':[{'origin':{'id':'mock_a','name':'水岸展馆'},'destination':{'id':'mock_b','name':'工艺馆'},
                          'mode':'walking','duration_s':600,'distance_m':500}]}])
    return req,plan


def build():
    result=[]
    def add(pid,pair,req,plan,error,rule):
        result.append({'probe_id':pid,'pair':pair,'origin':'new_synthetic_matched_pair',
            'expected_has_explicit_error':error,'focus':rule,'request':req.model_dump(mode='json'),'plan':plan.model_dump(mode='json')})
    req,plan=make_base()
    req.max_leg_walking_m=1000
    req.free_text_input += '只对相邻景点路线设置每段步行不超过1000米；酒店和餐馆接驳没有资料时保留待确认，不得当作达标。'
    add('transfer_unknown_disclosed','transfer',req,plan,False,'相邻景点步行500米已知；餐饮住宿接驳未知且已说明，不能将未知断言为矛盾/超限')
    overclaim=plan.model_copy(deep=True)
    overclaim.days[0].description='上午游览水岸展馆，午餐候选：晴川饭馆。午餐后自由休息，下午参观工艺馆。餐馆和酒店接驳均已核实，无需再确认；从餐馆步行至酒店确定200米，全部接驳已经满足1000米要求。'
    add('transfer_unknown_overclaim','transfer',req,overclaim,True,'结构化餐馆描述仍为实际接驳未核验，日介绍却声称已核实且确定200米，属于过度承诺')

    req,plan=make_base()
    req.free_text_input='一天可用于景点游览和相邻景点交通的时间上限为6小时，即360分钟；这一上限不包含餐饮、休息和酒店/餐馆接驳。每天安排两个景点，不得超出这个时间口径。其他未核验信息如实说明。'
    for a in plan.days[0].attractions:
        a.visit_duration=100
    plan.days[0].routes[0].duration_s=1800
    add('pace_within_explicit_budget','pace',req,plan,False,'游览200分钟＋已知交通30分钟=230分钟，低于明确360分钟上限，其余时间明确不计入该口径')
    overloaded=plan.model_copy(deep=True)
    for a in overloaded.days[0].attractions:
        a.visit_duration=200
    add('pace_exceeds_explicit_budget','pace',req,overloaded,True,'游览400分钟＋交通30分钟=430分钟，超过同一360分钟上限；不得将其当作无依据的节奏偏好')

    req,plan=make_base()
    plan.days[0].meals[0].name='松林食府（东门店）'
    plan.days[0].meals[0].poi_id='mock_meal_east'
    plan.days[0].description='上午游览水岸展馆，午餐候选：松林食府。午餐后自由休息，下午参观工艺馆。菜单、营业、房态和酒店/餐馆接驳均未核验。'
    plan.candidate_evidence=CandidateEvidence(request_id='synthetic_alias_capture',restaurant_candidates=[
        POIInfo(id='mock_meal_east',name='松林食府（东门店）'),POIInfo(id='mock_meal_east',name='松林食府'),
        POIInfo(id='mock_meal_west',name='松林食府（西门店）')])
    add('meal_alias_same_captured_id','identity',req,plan,False,'简称和完整名称由同次模拟快照关联同一ID，不能仅因文字不同判错')
    mismatch=plan.model_copy(deep=True)
    mismatch.days[0].description=mismatch.days[0].description.replace('午餐候选：松林食府。','午餐候选：松林食府（西门店）。')
    add('meal_same_brand_different_id','identity',req,mismatch,True,'相似品牌的东西门分店在快照中ID不同，日介绍选西门但列表为东门，需识别冲突')

    req,plan=make_base()
    req.free_text_input='每天至少1个、最多2个景点，兼顾休息。下雨时只安排室内活动。没有核验餐馆和酒店接驳就明确提示，不承诺营业、菜单、房态或预约已确认。'
    day=plan.days[0]
    day.attractions=[day.attractions[0]]
    day.attractions[0].visit_duration=90
    day.routes=[]
    day.description='上午在水岸展馆的室内展厅参观90分钟。午餐候选：晴川饭馆。下午留作自由休息，不新增户外活动；具体室内休息场所与酒店/餐馆实际接驳需自行确认，菜单、营业和房态均未核验。'
    plan.weather_info=[WeatherInfo(date=req.start_date,day_weather='小雨',night_weather='大雨',day_temp=18,night_temp=14,wind_direction='东',wind_power='1-3级')]
    add('rain_rest_with_unknown_transfers','rain_rest',req,plan,False,'雨天室内参观＋室内自由休息，与接驳待确认并不矛盾；保留1景点符合数量要求')
    outdoor=plan.model_copy(deep=True)
    outdoor.days[0].description=outdoor.days[0].description.replace('下午留作自由休息，不新增户外活动','下午去露天广场连续户外散步120分钟，无论下雨都执行')
    add('rain_outdoors_despite_indoor_requirement','rain_rest',req,outdoor,True,'同日期雨天且要求只室内，日介绍却明确安排雨天也执行的户外散步')
    for case in result:
        TripRequest.model_validate(case['request'])
        TripPlan.model_validate(case['plan'])
    return {'suite_version':'review_generalization_v1','status':'assistant_authored_draft_labels_not_independent_human',
            'rule':'预期在调用前固定；每对只变动一个待检因素；合成数据不冒充地图查询或真实旅行案例。',
            'cases':result}


if __name__=='__main__':
    target=Path(__file__).with_name('review_generalization_cases.json')
    target.write_text(json.dumps(build(),ensure_ascii=False,indent=2),encoding='utf-8')
    print('Prepared 8 predeclared synthetic cases in '+str(target))

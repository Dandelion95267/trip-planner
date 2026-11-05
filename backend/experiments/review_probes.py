"""固定输入的真实审核探测；仅 --live 才调用模型，不调用地图或重新规划。"""

import argparse
import asyncio
from collections import Counter
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import re
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.agents.review_agent import TripReviewAgent, TripReviewError
from app.config import get_settings
from app.models.schemas import TripPlan, TripRequest
from evaluation.run_eval import local_source_hash


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def cases(source):
    original = {r['case_id']: r for r in map(json.loads, (source/'records.jsonl').read_text(encoding='utf-8').splitlines())}
    result = []
    for key in ('dev_count_beijing_exact2', 'dev_count_hangzhou_max2'):
        row = original[key]
        plan = TripPlan.model_validate(row['result'])
        # 正常首次审核没有以前的结论；去除旧review，防止把待测答案泄漏给模型。
        plan.review = None
        result.append({'probe_id': 'original_' + key, 'origin': 'captured_live_plan_reaudit',
                       'expected_has_explicit_error': None,
                       'focus': '重审午餐顺序/雨天休息；允许其他有证据的问题，不以整体passed直接判误报',
                       'request': row['request'], 'plan': plan.model_dump(mode='json'),
                       'original_plan_sha256': digest(row['result'])})
    request = TripRequest(city='模拟城市', start_date='2026-11-05', end_date='2026-11-05', travel_days=1,
                          transportation='步行', route_mode='walking', accommodation='不住宿', enable_review=True,
                          free_text_input='每天安排两个景点，兼顾休息，午餐在两个景点之间。未知营业和实际接驳信息请说明。')
    plan = TripPlan(city=request.city, start_date=request.start_date, end_date=request.end_date,
                    overall_suggestions='这些是虚构地点的审核测试资料；仅根据给定内容审核，不推断未提供的菜单、营业和实际接驳。',
                    days=[{'date': request.start_date, 'day_index': 0, 'transportation': '步行', 'accommodation': '不住宿',
                           'description': '上午游览甲景点30分钟；之后在甲景点附近的餐馆甲午餐，菜单、营业和实际接驳未核实。午餐后自由休息，下午前往乙景点游览45分钟。',
                           'attractions': [{'poi_id': 'synthetic_a', 'name': '甲景点', 'address': '模拟地址甲', 'visit_duration': 30, 'description': '休闲参观。'},
                                           {'poi_id': 'synthetic_b', 'name': '乙景点', 'address': '模拟地址乙', 'visit_duration': 45, 'description': '休闲参观。'}],
                           'meals': [{'poi_id': 'synthetic_meal', 'type': 'lunch', 'name': '餐馆甲', 'anchor_poi_id': 'synthetic_a',
                                      'description': '甲景点附近的午餐候选，菜单、营业、实际接驳未核实。'}],
                           'routes': [{'origin': {'id': 'synthetic_a', 'name': '甲景点'}, 'destination': {'id': 'synthetic_b', 'name': '乙景点'},
                                       'mode': 'walking', 'duration_s': 600, 'distance_m': 500}],
                           }])
    base = {'origin': 'synthetic_matched_pair', 'request': request.model_dump(mode='json'), 'pair': 'lunch',
            'focus': '正常上午景点附近午餐后赴下午景点 vs 日介绍与结构化午餐名称明确不同'}
    result.append({**base, 'probe_id': 'lunch_consistent', 'expected_has_explicit_error': False, 'plan': plan.model_dump(mode='json')})
    bad = plan.model_copy(deep=True)
    bad.days[0].description = bad.days[0].description.replace('餐馆甲午餐', '餐馆乙午餐')
    result.append({**base, 'probe_id': 'lunch_name_conflict', 'expected_has_explicit_error': True, 'plan': bad.model_dump(mode='json')})
    rainy_request = request.model_copy(deep=True)
    rainy_request.free_text_input = '每天至少一个、最多两个景点，兼顾休息。下雨时只安排室内活动。'
    rainy = plan.model_copy(deep=True)
    day = rainy.days[0]
    day.attractions = [day.attractions[0].model_copy(update={'name': '甲博物馆', 'visit_duration': 60, 'description': '室内展厅参观，中途可休息。'})]
    day.routes = []
    day.meals = []
    day.description = '上午在甲博物馆室内看展60分钟，中途可休息；午餐自行安排，菜单和接驳未核实。下午根据体力自由休息，不额外安排户外活动。'
    # 固定的模拟预报，不是新地图实测；保留字段形状与真实审核一致。
    from app.models.schemas import WeatherInfo
    rainy.weather_info = [WeatherInfo(date='2026-11-05', day_weather='小雨', night_weather='小雨', day_temp=18, night_temp=14, wind_direction='东', wind_power='1-3级')]
    base = {'origin': 'synthetic_matched_pair', 'request': rainy_request.model_dump(mode='json'), 'pair': 'rain',
            'focus': '室内看展加自由休息 vs 明确室内要求与雨天户外散步冲突'}
    result.append({**base, 'probe_id': 'rain_indoor_rest', 'expected_has_explicit_error': False, 'plan': rainy.model_dump(mode='json')})
    bad_rain = rainy.model_copy(deep=True)
    bad_rain.days[0].description = '上午在甲博物馆室内看展60分钟，中途可休息；午餐自行安排，菜单和接驳未核实。下午安排在露天河岸步道户外散步2小时，即使下雨也按计划进行。'
    result.append({**base, 'probe_id': 'rain_outdoor_conflict', 'expected_has_explicit_error': True, 'plan': bad_rain.model_dump(mode='json')})
    for case in result:
        TripRequest.model_validate(case['request'])
        TripPlan.model_validate(case['plan'])
    return result


async def execute(args, prepared, source_hash):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder = args.output_root / f'{args.label}_{stamp}_{uuid4().hex[:8]}'
    folder.mkdir(parents=True, exist_ok=False)
    inputs_hash = digest(prepared)
    (folder/'inputs.json').write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding='utf-8')
    metadata = {'mode': 'live_review_only', 'label': args.label, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'source_run': str(args.source) if args.source else None,
                'source_run_records_sha256': hashlib.sha256((args.source/'records.jsonl').read_bytes()).hexdigest() if args.source else None,
                'fixture_path': str(args.fixtures) if args.fixtures else None,
                'fixture_sha256': hashlib.sha256(args.fixtures.read_bytes()).hexdigest() if args.fixtures else None,
                'local_source_sha256': source_hash, 'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'inputs_sha256': inputs_hash, 'repeats': args.repeats, 'planned_calls': len(prepared)*args.repeats,
                'case_order': [c['probe_id'] for c in prepared],
                'model_id_from_local_configuration': get_settings().llm_model_id,
                'map_calls': 0, 'full_trip_generation_calls': 0,
                'label_note': '合成预期在调用前固定，为开发助手标签，未独立人工审核；原案例只做定性重审，不以整体通过率判目标误报。'}
    (folder/'run.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    logging.disable(logging.CRITICAL)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        reviewer = TripReviewAgent()
    rows = []
    with (folder/'records.jsonl').open('x', encoding='utf-8') as stream:
        for rep in range(1, args.repeats+1):
            for case in prepared:
                req = TripRequest.model_validate(case['request'])
                plan = TripPlan.model_validate(case['plan'])
                before = digest({'request': req.model_dump(mode='json'), 'plan': plan.model_dump(mode='json')})
                start = perf_counter()
                row = {'probe_id': case['probe_id'], 'repetition': rep, 'origin': case['origin'],
                       'expected_has_explicit_error': case['expected_has_explicit_error'], 'review': None, 'error': None}
                try:
                    # 隐去SDK输出，只保存结构化审核结论；不把上游日志或私密配置写进结果。
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        review = await reviewer.review_trip(req, plan)
                    row['review'] = review.model_dump(mode='json')
                except Exception as error:
                    chain = []
                    cause = error
                    while cause is not None and len(chain) < 6:
                        chain.append(type(cause).__name__)
                        cause = cause.__cause__
                    row['error'] = {'type': type(error).__name__, 'cause_types': chain,
                                    'message': '审核调用或结构校验失败；不计为通过，不自动重试。'}
                row['elapsed_s'] = round(perf_counter()-start, 4)
                row['input_unchanged'] = before == digest({'request': req.model_dump(mode='json'), 'plan': plan.model_dump(mode='json')})
                rows.append(row)
                stream.write(json.dumps(row, ensure_ascii=False)+'\n')
                stream.flush()
                print(f"{case['probe_id']} [{rep}] passed={row['review']['passed'] if row['review'] else None} error={row['error']['type'] if row['error'] else None}", flush=True)
    assert source_hash == local_source_hash()
    assert digest(json.loads((folder/'inputs.json').read_text(encoding='utf-8'))) == inputs_hash
    summary = {'runs': len(rows), 'completed_reviews': sum(r['review'] is not None for r in rows),
               'errors': sum(r['error'] is not None for r in rows), 'all_inputs_unchanged': all(r['input_unchanged'] for r in rows),
               'by_case': {c['probe_id']: dict(Counter('error' if r['error'] else 'passed' if r['review']['passed'] else 'issues_reported'
                                                    for r in rows if r['probe_id']==c['probe_id'])) for c in prepared},
               'cost_tokens': 'not_collected', 'independent_human_labels': 'not_available',
               'semantic_status_counts': dict(Counter((r['review'] or {}).get('semantic_status') or 'legacy_or_error' for r in rows)),
               'notes': 'issues_reported不是自动确认误报；需要对照固定输入逐项核对，控制案例也需要核实是否命中真正矛盾。'}
    (folder/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Saved review probes: ' + str(folder), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    origin = parser.add_mutually_exclusive_group(required=True)
    origin.add_argument('--source', type=Path)
    origin.add_argument('--fixtures', type=Path, help='预先固定的合成审核样例JSON；不是新增真实旅行数据')
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--label', default='review_probes_v7')
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.repeats <= 3:
        parser.error('repeats must be between 1 and 3')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.label):
        parser.error('label must use 1-64 letters, digits, underscores or hyphens')
    if args.fixtures:
        fixture = json.loads(args.fixtures.read_text(encoding='utf-8'))
        prepared = fixture['cases']
        if not prepared or len({c['probe_id'] for c in prepared}) != len(prepared):
            parser.error('fixtures must contain unique nonempty probe IDs')
        for case in prepared:
            TripRequest.model_validate(case['request'])
            TripPlan.model_validate(case['plan'])
            if case['expected_has_explicit_error'] not in (True, False, None) or not case.get('origin'):
                parser.error('each fixture needs origin and a predeclared expected label')
    else:
        prepared = cases(args.source)
    if not args.live:
        print(json.dumps({'mode': 'preview', 'probes': [c['probe_id'] for c in prepared], 'planned_calls': len(prepared)*args.repeats, 'inputs_sha256': digest(prepared)}, ensure_ascii=False))
        return
    asyncio.run(execute(args, prepared, local_source_hash()))


if __name__ == '__main__':
    main()

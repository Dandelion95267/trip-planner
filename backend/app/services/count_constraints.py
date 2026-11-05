"""保守识别明确的每日景点数量；只读用户需求，不让模型自行决定约束。"""

from dataclasses import asdict, dataclass
import re

from app.models.schemas import ReviewAlternative, TripPlan, TripRequest, TripReviewIssue


NUMBER = r"(?:[0-9]{1,3}|[一二两三四五六七八九十]{1,3})"
FILLER = r"(?:(?:恰好|正好|刚好|必须|需要|要|只|安排|游览|参观)\s*){0,3}"


def _number(text):
    if text.isdigit():
        return int(text)
    units = {c:i for i,c in enumerate('零一二三四五六七八九')}
    text = text.replace('两','二')
    if '十' in text:
        left,right = text.split('十',1)
        if (not left or left in units) and (not right or right in units):
            return (units[left] if left else 1)*10+(units[right] if right else 0)
    return units.get(text)


@dataclass(frozen=True)
class DailyCountRule:
    status: str = 'not_recognized'
    minimum: int | None = None
    maximum: int | None = None
    evidence: tuple[str,...] = ()

    def describe(self):
        if self.minimum == self.maximum and self.minimum is not None:
            return f'每天恰好{self.minimum}个景点'
        if self.minimum is not None and self.maximum is not None:
            return f'每天至少{self.minimum}个、最多{self.maximum}个景点'
        if self.minimum is not None:
            return f'每天至少{self.minimum}个景点'
        return f'每天最多{self.maximum}个景点'

    def for_prompt(self):
        return {**asdict(self), 'meaning': self.describe() if self.status == 'recognized' else '数量要求未能明确识别，不能猜测或自行放宽。'}


def daily_count_rule(request: TripRequest) -> DailyCountRule:
    text = re.sub(r'[ \t]','',request.free_text_input or '')
    lower,upper,evidence = [],[],[]
    for match in re.finditer(r'(?:每天|每日|每一天)[^。；;\n]{0,60}?景点',text):
        core = match.group()
        prefix = text[max(0,match.start()-8):match.start()].rstrip()
        tail = text[match.end():]
        if re.search(r'(?:不要|不必|不用|不要求|并非|例如|比如)(?:严格)?$',prefix):
            continue
        if any(w in core for w in ('大概','左右','尽量','可能','如果','或者','上午','下午','晚上')) or tail.startswith(('左右','上下')):
            continue
        # 不把有条件的要求转换为无条件硬约束。
        sentence_prefix = re.split(r'[。；;\n]',text[:match.start()])[-1]
        sentence_tail = re.split(r'[。；;\n]',tail)[0]
        if any(w in sentence_prefix + core + sentence_tail for w in ('如果','若','视情况')) or re.match(r'^[、，,]?(?:或者|或|也可|可以)',sentence_tail):
            continue
        extension = re.match(rf'\s*[、，,]\s*(?:至少|最少|不少于|最多|至多|不超过)\s*(?:安排)?\s*{NUMBER}个景点',tail)
        if extension:
            core += extension.group()
        scope = re.sub(r'^(?:每天|每日|每一天)','',core).strip()
        lo = re.findall(rf'(?:至少|最少|不少于)\s*(?:安排|游览|参观)?\s*({NUMBER})个(?:景点)?(?=$|[、，,\s])',scope)
        hi = re.findall(rf'(?:最多|至多|不超过)\s*(?:安排|游览|参观)?\s*({NUMBER})个(?:景点)?(?=$|[、，,\s])',scope)
        span = re.fullmatch(rf'{FILLER}({NUMBER})\s*(?:至|到|~|～|-)\s*({NUMBER})个景点',scope)
        exact = re.fullmatch(rf'{FILLER}({NUMBER})个景点',scope)
        lows, highs = [_number(x) for x in lo], [_number(x) for x in hi]
        if span:
            lows.append(_number(span.group(1))); highs.append(_number(span.group(2)))
        elif exact:
            lows.append(_number(exact.group(1))); highs.append(_number(exact.group(1)))
        if any(x is None or x <= 0 for x in lows+highs):
            continue
        if lows or highs:
            lower.extend(lows); upper.extend(highs); evidence.append(core)
    minimum,maximum = max(lower,default=None),min(upper,default=None)
    status = 'recognized' if evidence else 'not_recognized'
    if minimum is not None and maximum is not None and minimum > maximum:
        status='ambiguous'
    return DailyCountRule(status,minimum,maximum,tuple(evidence))


def count_issues(request: TripRequest, plan: TripPlan) -> list[TripReviewIssue]:
    rule = daily_count_rule(request)
    if rule.status != 'recognized':
        return []
    result=[]
    for day in plan.days:
        count=len(day.attractions)
        if (rule.minimum is not None and count < rule.minimum) or (rule.maximum is not None and count > rule.maximum):
            result.append(TripReviewIssue(category='preference',day_index=day.day_index,source='program',
                problem='当天景点数量未满足原明确要求。',evidence=f'原要求：{rule.describe()}；第{day.day_index+1}天实际{count}个景点。',
                suggestion=f'保持{rule.describe()}及原交通、步行限制，重新安排候选景点；无法满足时说明原因，改变数量需要用户确认后修改需求并重新规划。'))
    return result


def _changes_count(suggestion):
    for match in re.finditer(r'(?:减少|删减|删除|删去|取消|增加|添加|增设)[^，,。；;]{0,8}?景点|(?:仅|只)保留[^，,。；;]{0,4}?个景点',suggestion):
        before=suggestion[max(0,match.start()-12):match.start()]
        after=suggestion[match.end():]
        if re.search(r'(?:不|不得|不要|不应|不能|不可|不必|无需|避免)(?:通过|以|靠)?$',before):
            continue
        if re.match(r'(?:的)?(?:游览|游玩|停留)(?:时长|时间)',after):
            continue
        return True
    return False


def constrain_suggestions(request: TripRequest, issues: list[TripReviewIssue]) -> list[TripReviewIssue]:
    rule = daily_count_rule(request)
    if rule.status != 'recognized':
        return issues
    result=[]
    for issue in issues:
        item=issue.model_copy(deep=True)
        prefix=f'保持{rule.describe()}及原交通方式、步行限制。'
        if issue.source == 'model' and rule.minimum == rule.maximum and _changes_count(issue.suggestion):
            item.alternative=ReviewAlternative(proposal=issue.suggestion)
            item.suggestion=prefix+'优先调整景点位置、顺序或合理游览时长，不得编造或过度压缩；仍无法满足时说明原因，等待用户选择，不直接改变数量。'
        elif issue.source == 'model':
            item.suggestion=prefix+issue.suggestion+' 若需要改变这些要求，须先由用户确认并修改需求后重新规划，不能直接采用。'
        result.append(item)
    return result

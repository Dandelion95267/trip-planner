"""有界餐饮偏好解释和基于地图字段的筛选；不推测菜单、做法或饮食安全。"""

from dataclasses import dataclass
import re

from app.models.schemas import DiningPreferenceEvidence, POIInfo, TripRequest


CUISINES = {
    "川菜": ("四川菜", "川菜"), "湘菜": ("湖南菜", "湘菜"),
    "粤菜": ("广东菜", "粤菜"), "东北菜": ("东北菜",),
    "京菜": ("北京菜", "京菜"), "杭帮菜": ("杭帮菜",),
    "越南菜": ("越南菜",), "泰国菜": ("泰国菜", "泰餐"),
    "日本料理": ("日本料理", "日料", "日本菜"),
    "韩国料理": ("韩国料理", "韩餐", "韩国菜"), "西餐": ("西餐",),
    "面食": ("面馆", "面食"),
}
# 可审阅的有限城市检索配置；其他城市的“本地菜”保持待确认，不让模型猜测。
LOCAL_CATEGORY_HINTS = {"成都": "川菜"}
LOCAL_TERMS = ("本地菜", "当地菜", "本地美食", "当地美食", "本地餐饮", "当地餐饮", "本地小吃", "当地小吃")
TASTE_TERMS = ("口味清淡", "清淡", "少油", "少盐", "不要辣", "不辣", "微辣")
DIET_TERMS = ("无麸质", "纯素", "素食", "清真")
STRICT_MARKERS = ("必须", "严格", "只吃", "只能吃", "不能吃", "不吃", "不要", "过敏", "忌口")
NO_RESTRICTIONS = ("没有过敏史", "没有过敏", "不过敏", "无过敏", "没有忌口", "无忌口", "不需要忌口")


@dataclass(frozen=True)
class Criterion:
    label: str
    kind: str
    target: str | None = None
    strict: bool = False


@dataclass(frozen=True)
class PreferenceProfile:
    requested: str
    criteria: tuple[Criterion, ...]
    queries: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def signature(self):
        return tuple((x.label, x.kind, x.target, x.strict) for x in self.criteria)


def build_profile(request: TripRequest) -> PreferenceProfile:
    raw = request.dining_preference.strip()
    extra = request.free_text_input or ""
    criteria = []
    notes = []
    remaining = raw
    main_strict = any(word in raw for word in STRICT_MARKERS)

    def add(label, kind, target=None, aliases=()):
        # 只读取与该偏好相邻的明确严格措辞，不把“必须公交”等路线要求变成饮食限制。
        strict_extra = any(re.search(r"(?:必须|严格|只吃|只能吃)[^，,。；;\n]{0,8}" + re.escape(term), extra)
                           for term in (label, *aliases))
        negative_taste = any(term.startswith("不要") and term in extra for term in aliases)
        item = Criterion(label, kind, target, main_strict or bool(strict_extra) or negative_taste or kind == "dietary")
        if not any((x.label, x.kind) == (item.label, item.kind) for x in criteria):
            criteria.append(item)

    local = next((t for t in LOCAL_TERMS if t in raw or t in extra), None)
    if local:
        target = LOCAL_CATEGORY_HINTS.get(request.city.strip().removesuffix("市"))
        add("本地菜", "cuisine", target, LOCAL_TERMS)
        notes.append(f"“本地菜”按{request.city}的{target}类别方向检索；类别不代表具体菜单已核实。" if target else f"尚未配置{request.city}的本地菜类别对应关系，不能自动确认菜系匹配。")
        for term in LOCAL_TERMS:
            remaining = remaining.replace(term, " ")
    for canonical, aliases in CUISINES.items():
        if any(alias in remaining for alias in aliases):
            add(canonical, "cuisine", canonical, aliases)
            for alias in aliases:
                remaining = remaining.replace(alias, " ")
    for term in TASTE_TERMS:
        if term in remaining or term in extra:
            label = "清淡" if term in {"清淡", "口味清淡"} else "不辣" if term == "不要辣" else term
            add(label, "taste", aliases=(term,))
            remaining = remaining.replace(term, " ")
    combined = raw + "。" + extra
    dietary_text = combined
    for phrase in NO_RESTRICTIONS:
        dietary_text = dietary_text.replace(phrase, " ")
    for term in DIET_TERMS:
        if term in combined:
            add(term, "dietary")
            remaining = remaining.replace(term, " ")
    for restriction in re.findall(r"(?:不吃|不能吃|忌口|过敏)[^，,。；;\n]{1,16}", dietary_text):
        add(restriction, "dietary")
        remaining = remaining.replace(restriction, " ")
    for sentence in re.split(r"[，,。；;\n]", dietary_text):
        if any(word in sentence for word in ("过敏", "忌口")):
            add(sentence.strip()[:60], "dietary")
    for filler in (*STRICT_MARKERS, "希望", "尽量", "口味", "和", "以及", "的", "菜系"):
        remaining = remaining.replace(filler, " ")
    for term in filter(None, re.split(r"[\s,，、;；。]+", remaining)):
        add(term, "other")
    if raw and not criteria:
        add(raw, "other")
    if criteria:
        notes.append("菜系按同次地图类别或名称线索分别说明；清淡做法、菜单与饮食限制必须联系商家进一步核实。")
    primary = raw or " ".join(x.label for x in criteria)
    queries = [primary[:100]]
    if criteria and not any(x.strict for x in criteria):
        cuisines = [x.target or x.label for x in criteria if x.kind == "cuisine"]
        fallback = " ".join(dict.fromkeys(cuisines)) if cuisines else "" if all(x.kind == "taste" for x in criteria) else primary
        if fallback != primary:
            queries.append(fallback)
    return PreferenceProfile(request.dining_preference, tuple(criteria), tuple(queries[:2]), tuple(notes))


def _labels(text: str) -> set[str]:
    found = {key for key, aliases in CUISINES.items() if any(alias in text for alias in aliases)}
    if "川湘菜" in text:
        found.update({"川菜", "湘菜"})
    return found


def assess(profile: PreferenceProfile, poi: POIInfo) -> list[DiningPreferenceEvidence]:
    category, name = _labels(poi.type), _labels(poi.name)
    result = []
    for criterion in profile.criteria:
        status, source, value = "unknown", "not_available", None
        text = "现有地图资料不足以核实该要求，需要联系商家确认。"
        if criterion.kind == "cuisine" and criterion.target:
            target = criterion.target
            if category and target not in category:
                status, source, value = "conflict", "poi_type", poi.type
                text = "地图类别与所需类别方向不一致，未作为偏好匹配候选。"
            elif name and target not in name:
                status, source, value = "conflict", "poi_name", poi.name
                text = "名称明确指向其他菜系，不能作为所需类别的匹配依据。"
            elif target in category:
                status, source, value = "supported", "poi_type", poi.type
                text = "地图类别支持该检索方向；不代表菜单、做法或营业条件已核实。"
            elif target in name:
                status, source, value = "hint", "poi_name", poi.name
                text = "名称提供类别线索，但缺少地图类别确认，需进一步核实。"
        elif criterion.kind == "taste":
            text = "地图类别或店名不能证明实际口味与做法，需联系商家确认。"
        elif criterion.kind == "dietary":
            text = "现有资料不能确认该饮食限制，未将其视为已满足；需联系商家核实。"
        result.append(DiningPreferenceEvidence(
            preference=criterion.label, kind=criterion.kind, target_category=criterion.target,
            strict=criterion.strict, status=status, source=source, value=value, explanation=text,
        ))
    return result


def rank_key(evidence):
    return (-sum(e.status == "supported" for e in evidence), -sum(e.status == "hint" for e in evidence))


def rejection_reason(evidence):
    if any(e.status == "conflict" for e in evidence):
        return "preference_conflict"
    if any(e.strict and e.status != "supported" for e in evidence):
        return "preference_unverified"
    return None


def preference_level(evidence):
    if not evidence:
        return "not_requested"
    if any(e.status == "supported" for e in evidence):
        return "category_supported"
    if any(e.status == "hint" for e in evidence):
        return "name_hint"
    return "unverified"


def preference_summary(evidence):
    if not evidence:
        return None
    labels = {"supported": "有地图类别依据", "hint": "仅有名称线索", "unknown": "尚未核实", "conflict": "类别冲突"}
    return "；".join(f"{e.preference}：{labels[e.status]}" for e in evidence) + "。请联系商家确认具体菜单、口味与做法。"

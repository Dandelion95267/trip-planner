"""在工具解析后记录资料，不从模型答案构造来源。只在一次请求中使用。"""

from datetime import datetime, timezone
from uuid import uuid4

from app.models.schemas import CandidateEvidence, POIDetailEvidence, POIInfo, POISearchEvidence


class SourceEvidenceRecorder:
    def __init__(self):
        self.request_id = uuid4().hex
        self.searches: list[POISearchEvidence] = []
        self.details: dict[str, POIDetailEvidence] = {}
        self.groups: dict[str, dict[str, POIInfo]] = {
            "candidates": {}, "hotel_candidates": {}, "restaurant_candidates": {},
        }

    def search(self, tool: str, parameters: dict, pois: list[POIInfo]) -> None:
        allowed = {key: value for key, value in parameters.items()
                   if key in {"keywords", "city", "citylimit", "location", "radius", "strategy"}}
        self.searches.append(POISearchEvidence(
            tool=tool, parameters=allowed,
            collected_at_utc=datetime.now(timezone.utc).isoformat(),
            pois=[poi.model_copy(deep=True) for poi in pois],
        ))

    def detail(self, poi: POIInfo) -> None:
        self.details[poi.id] = POIDetailEvidence(
            collected_at_utc=datetime.now(timezone.utc).isoformat(), poi=poi.model_copy(deep=True),
        )

    def register(self, group: str, pois: list[POIInfo]) -> None:
        for poi in pois:
            # 保留原始候选字段；坐标后补的依据单独来自searches/details。
            self.groups[group].setdefault(poi.id, poi.model_copy(deep=True))

    def snapshot(self) -> CandidateEvidence:
        return CandidateEvidence(
            request_id=self.request_id,
            **{group: list(pois.values()) for group, pois in self.groups.items()},
            searches=self.searches, details=list(self.details.values()),
        ).model_copy(deep=True)

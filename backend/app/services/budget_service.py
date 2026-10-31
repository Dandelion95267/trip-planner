"""按用户填写的费用标准计算预算，不调用模型，也不推测实际报价。"""

from app.models.schemas import Budget, BudgetItem, BudgetOptions, TripPlan


def estimate_budget(plan: TripPlan, options: BudgetOptions) -> Budget:
    """使用已校验的最终行程计数，不修改景点、酒店或餐馆的价格字段。"""
    days = len(plan.days)
    nights = max(days - 1, 0)
    visits = sum(len(day.attractions) for day in plan.days)
    people = options.travelers
    rooms = options.rooms
    rows = [
        ("attractions", "景点门票", visits * people, options.ticket_per_person_visit,
         f"{visits}次景点游览 × {people}人 × 每人每次平均门票"),
        ("hotels", "住宿", nights * rooms, options.hotel_per_room_night,
         f"{nights}晚 × {rooms}间 × 每间每晚费用"),
        ("meals", "餐饮", days * people, options.meals_per_person_day,
         f"{days}天 × {people}人 × 每人每天全部餐饮费用"),
        ("transportation", "市内交通", days * people, options.transport_per_person_day,
         f"{days}天 × {people}人 × 每人每天市内交通费用"),
    ]
    items = []
    for category, label, quantity, rate, basis in rows:
        # 无需该项支出（例如当天往返不住宿）可以确定为0；其他缺失单价不补0。
        subtotal = 0 if quantity == 0 else (quantity * rate if rate is not None else None)
        items.append(BudgetItem(
            category=category, label=label, quantity=quantity, unit_price=rate,
            subtotal=subtotal, basis=basis,
        ))
    amounts = {item.category: item.subtotal for item in items}
    known_subtotal = sum(item.subtotal for item in items if item.subtotal is not None)
    complete = all(item.subtotal is not None for item in items)
    return Budget(
        travelers=people, rooms=rooms, nights=nights,
        total_attractions=amounts["attractions"], total_hotels=amounts["hotels"],
        total_meals=amounts["meals"], total_transportation=amounts["transportation"],
        known_subtotal=known_subtotal, is_complete=complete,
        total=known_subtotal if complete else None, items=items,
        notes=[
            "按用户填写的标准估算，不是实时房价、票价、餐费或交通报价，也不限制规划花费。",
            "住宿按旅行天数减1晚计算，不含提前到达或行程结束后续住；按房间计费，不再乘人数。",
            "门票按最终行程的每次景点游览计算，重复游览也计次数；餐饮按全天计算，不只包含推荐餐馆。",
            "只估算门票、住宿、餐饮和市内交通，不含往返目的地的大交通、购物及其他费用。",
            "未填写的费用保持未知；部分项目小计不代表完整旅行费用。",
        ],
    )

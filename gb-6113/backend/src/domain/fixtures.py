BASE = {
    "quorum": 2,
    "failure_at": 105,
    "promotion": "北坡机房",
    "standbys": [
        {"name": "北坡机房", "flush_ms": 8, "outages": [[40, 120]]},
        {"name": "山脚机房", "flush_ms": 13, "outages": [[59, 76]]},
    ],
    "writes": [
        {"code": "SN-107", "area": "松槽道", "arrival": 12, "local_ms": 4, "fallback": False},
        {"code": "SN-108", "area": "石岭道", "arrival": 42, "local_ms": 4, "fallback": True},
        {"code": "SN-109", "area": "北坡道", "arrival": 57, "local_ms": 5, "fallback": False},
        {"code": "SN-110", "area": "松槽道", "arrival": 72, "local_ms": 3, "fallback": True},
        {"code": "SN-111", "area": "山脚道", "arrival": 93, "local_ms": 6, "fallback": True},
    ],
}
FIXTURES = [
    {
        "code": "SN-1124-BREAK",
        "title": "北坡机房线路维护",
        "description": "北坡接收链路恰在首条完结后断开；比较严格确认与允许降级的班次写入。",
        "payload": BASE,
    },
    {
        "code": "SN-1124-DURABLE",
        "title": "山脚机房恢复来源",
        "description": "故障时从山脚机房恢复，验证降级确认是否能由另一条副本链路保留。",
        "payload": {**BASE, "promotion": "山脚机房"},
    },
    {
        "code": "SN-1124-CAPACITY",
        "title": "交班集中写入·容量入场排队",
        "description": "剧本容量为 1：凭条满位后排队，必保凭条越过普通凭条先入场；标注故障时仍在排队、已入场未答复、已答复但目标缺失的凭条。",
        "payload": {**BASE, "capacity": 1},
    },
]

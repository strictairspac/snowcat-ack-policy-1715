import unittest

from src.domain.fixtures import FIXTURES
from src.domain.inputs import parse
from src.domain.scenario import run

QUEUED = "故障时仍在排队"
ADMITTED = "已入场未答复"
MISSING = "已答复但目标缺失"
RECOVERABLE = "已答复且目标可恢复"
LATE = "故障后到达未入场"


def mixed_payload(crash):
    return {
        "quorum": 2,
        "failure_at": crash,
        "promotion": "R1",
        "capacity": 1,
        "standbys": [
            {"name": "R1", "flush_ms": 10, "outages": [[10, 30]]},
            {"name": "R2", "flush_ms": 4, "outages": []},
        ],
        "writes": [
            {"code": "W1", "area": "a", "arrival": 0, "local_ms": 5, "fallback": True},
            {"code": "W2", "area": "a", "arrival": 1, "local_ms": 5, "fallback": False},
            {"code": "W3", "area": "a", "arrival": 2, "local_ms": 5, "fallback": True},
            {"code": "W4", "area": "a", "arrival": 4, "local_ms": 5, "fallback": False},
        ],
    }


class CapacityInputTests(unittest.TestCase):
    def test_capacity_bounds(self):
        base = mixed_payload(20)
        for bad in (0, -1, 201, "3", 1.0, True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse({**base, "capacity": bad})

    def test_valid_capacity_accepted(self):
        replicas, writes, quorum, crash, promoted, capacity = parse(mixed_payload(20))
        self.assertEqual(capacity, 1)


class CapacitySimulationTests(unittest.TestCase):
    def _by_code(self, result):
        return {row["写入号"]: row for row in result["rows"]}

    def test_three_fault_buckets_at_crash(self):
        result = run(mixed_payload(20), 3, None)
        self.assertEqual(result["metrics"], {QUEUED: 2, ADMITTED: 1, MISSING: 1})
        rows = self._by_code(result)
        self.assertEqual(rows["W1"]["故障时状态"], MISSING)
        self.assertEqual(rows["W1"]["按策略答复 ms"], 8)
        self.assertEqual(rows["W1"]["答复路径"], "本地降级")
        self.assertIsNone(rows["W1"]["目标落盘 ms"])
        self.assertEqual(rows["W2"]["故障时状态"], ADMITTED)
        self.assertEqual(rows["W2"]["入场 ms"], 8)
        self.assertEqual(rows["W3"]["故障时状态"], QUEUED)
        self.assertEqual(rows["W4"]["故障时状态"], QUEUED)

    def test_urgent_overtakes_normal_in_queue(self):
        # 故障足够晚：W4（必保，t=4 到）必须越过 W3（普通，t=2 到）先入场。
        result = run(mixed_payload(100), 3, None)
        rows = self._by_code(result)
        self.assertLess(rows["W4"]["入场 ms"], rows["W3"]["入场 ms"])
        self.assertEqual(rows["W4"]["入场 ms"], 45)
        self.assertEqual(rows["W3"]["入场 ms"], 60)
        self.assertTrue(all(row["故障时状态"] == RECOVERABLE for row in result["rows"]))

    def test_same_tick_reply_frees_slot_before_allocation(self):
        payload = {
            "quorum": 1, "failure_at": 50, "promotion": "R1", "capacity": 1,
            "standbys": [{"name": "R1", "flush_ms": 100, "outages": []}],
            "writes": [
                {"code": "W1", "area": "a", "arrival": 0, "local_ms": 5, "fallback": True},
                {"code": "W2", "area": "a", "arrival": 8, "local_ms": 5, "fallback": True},
                {"code": "W3", "area": "a", "arrival": 8, "local_ms": 5, "fallback": False},
            ],
        }
        rows = self._by_code(run(payload, 3, None))
        self.assertEqual(rows["W3"]["入场 ms"], 8)
        self.assertIsNone(rows["W2"]["入场 ms"])
        self.assertEqual(rows["W2"]["故障时状态"], QUEUED)

    def test_reply_exactly_at_crash_counts_as_responded(self):
        payload = {
            "quorum": 1, "failure_at": 15, "promotion": "R1", "capacity": 1,
            "standbys": [{"name": "R1", "flush_ms": 10, "outages": []}],
            "writes": [{"code": "W1", "area": "a", "arrival": 0, "local_ms": 5, "fallback": False}],
        }
        rows = self._by_code(run(payload, 999, None))
        self.assertEqual(rows["W1"]["故障时状态"], RECOVERABLE)
        self.assertEqual(rows["W1"]["按策略答复 ms"], 15)

        payload["failure_at"] = 14
        rows = self._by_code(run(payload, 999, None))
        self.assertEqual(rows["W1"]["故障时状态"], ADMITTED)

    def test_arrival_at_crash_admitted_later_arrival_excluded(self):
        payload = {
            "quorum": 1, "failure_at": 10, "promotion": "R1", "capacity": 2,
            "standbys": [{"name": "R1", "flush_ms": 100, "outages": []}],
            "writes": [
                {"code": "W1", "area": "a", "arrival": 0, "local_ms": 5, "fallback": False},
                {"code": "W2", "area": "a", "arrival": 10, "local_ms": 5, "fallback": False},
                {"code": "W3", "area": "a", "arrival": 11, "local_ms": 5, "fallback": False},
            ],
        }
        rows = self._by_code(run(payload, 999, None))
        self.assertEqual(rows["W2"]["入场 ms"], 10)
        self.assertEqual(rows["W3"]["故障时状态"], LATE)

    def test_degraded_reply_still_replicates_to_replica(self):
        # 降级在 t=8 答复，但 R1 中断后恢复，最终在 t=35 补写完成（中断窗暂停消耗）。
        result = run(mixed_payload(100), 3, None)
        w1 = self._by_code(result)["W1"]
        self.assertEqual(w1["答复路径"], "本地降级")
        self.assertEqual(w1["按策略答复 ms"], 8)
        self.assertEqual(w1["目标落盘 ms"], 35)

    def test_capacity_summary_mentions_three_buckets(self):
        result = run(mixed_payload(20), 3, None)
        self.assertIn("剧本容量 1", result["summary"])
        for label in ("仍在排队 2 笔", "已入场未答复 1 笔", "找不到 1 笔"):
            self.assertIn(label, result["summary"])

    def test_demo_fixture_exposes_all_three_buckets(self):
        fixture = next(item for item in FIXTURES if item["code"] == "SN-1124-CAPACITY")
        self.assertEqual(fixture["payload"]["capacity"], 1)
        result = run(fixture["payload"], 25, None)
        self.assertEqual(result["metrics"], {QUEUED: 2, ADMITTED: 1, MISSING: 1})


if __name__ == "__main__":
    unittest.main()

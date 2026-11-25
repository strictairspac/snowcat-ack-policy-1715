import unittest

from src.domain.fixtures import FIXTURES
from src.domain.inputs import parse
from src.domain.scenario import run


class LegacyScenarioTests(unittest.TestCase):
    """旧剧本不填容量时，必须沿用串行落盘的原始口径与数值。"""

    def test_fixtures_keep_original_results(self):
        expected = {
            "SN-1124-BREAK": {"严格答复": 1, "策略答复": 3, "已答复缺失": 2},
            "SN-1124-DURABLE": {"严格答复": 1, "策略答复": 3, "已答复缺失": 0},
        }
        for fixture in FIXTURES:
            if fixture["code"] not in expected:
                continue
            with self.subTest(fixture=fixture["code"]):
                result = run(fixture["payload"], 25, None)
                self.assertEqual(result["metrics"], expected[fixture["code"]])
                self.assertNotIn("容量", result["summary"])

    def test_missing_capacity_parses_as_none(self):
        *_, capacity = parse(FIXTURES[0]["payload"])
        self.assertIsNone(capacity)

    def test_explicit_null_capacity_parses_as_none(self):
        payload = {**FIXTURES[0]["payload"], "capacity": None}
        *_, capacity = parse(payload)
        self.assertIsNone(capacity)
        # null 容量走旧路径，仍返回旧版三指标。
        result = run(payload, 25, None)
        self.assertEqual(set(result["metrics"]), {"严格答复", "策略答复", "已答复缺失"})


if __name__ == "__main__":
    unittest.main()

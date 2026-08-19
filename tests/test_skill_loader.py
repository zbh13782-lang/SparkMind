from __future__ import annotations

import unittest

from sparkos.agent.skills.loader import infer_skill_name, load_skills


class SkillLoaderTests(unittest.TestCase):
    def test_quality_test_skill_is_discoverable(self) -> None:
        skills = load_skills()

        self.assertIn("data-quality-test", {skill.name for skill in skills})

    def test_plain_query_does_not_activate_quality_test(self) -> None:
        skills = load_skills()

        self.assertIsNone(infer_skill_name("查询最近七天的销售额", skills))

    def test_quality_request_without_query_does_not_activate_quality_test(self) -> None:
        skills = load_skills()

        self.assertIsNone(infer_skill_name("介绍一下数据质量测试有哪些维度", skills))

    def test_query_and_quality_test_activates_quality_test(self) -> None:
        skills = load_skills()

        self.assertEqual(
            infer_skill_name("查询最近七天的销售额，并做质量测试", skills),
            "data-quality-test",
        )

    def test_query_and_data_quality_check_activates_quality_test(self) -> None:
        skills = load_skills()

        self.assertEqual(
            infer_skill_name("查一下订单明细，同时检查数据质量", skills),
            "data-quality-test",
        )

    def test_query_and_check_quality_activates_quality_test(self) -> None:
        skills = load_skills()

        self.assertEqual(
            infer_skill_name("帮我查询top5的产品并检查质量", skills),
            "data-quality-test",
        )

    def test_query_and_analyze_quality_activates_quality_test(self) -> None:
        skills = load_skills()

        self.assertEqual(
            infer_skill_name("帮我查询销量top5的产品，分析一下质量", skills),
            "data-quality-test",
        )


if __name__ == "__main__":
    unittest.main()

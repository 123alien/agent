import unittest

from app.api.tasks import _merge_review, _review_progress
from app.schemas.task import ReviewItem, ReviewRequest, TaskRecord


def review_task() -> TaskRecord:
    return TaskRecord(
        task_id="T1",
        project_id="P1",
        project_name="复核测试",
        check_type="full",
        status="waiting_review",
        review_request={"issues": [{"issue_id": "I1"}, {"issue_id": "I2"}]},
        created_at="2026-08-08T00:00:00+08:00",
        updated_at="2026-08-08T00:00:00+08:00",
    )


class ReviewWorkflowTests(unittest.TestCase):
    def test_partial_review_is_merged_and_reports_remaining(self) -> None:
        task = review_task()
        first = ReviewRequest(
            reviewer="张三",
            submit=False,
            items=[ReviewItem(issue_id="I1", decision="误判", comment="模板条款")],
        )
        merged = _merge_review(task, {}, first)
        progress = _review_progress(task, merged)
        self.assertEqual(progress["reviewed"], 1)
        self.assertEqual(progress["missing_issue_ids"], ["I2"])

        second = ReviewRequest(
            reviewer="张三",
            items=[ReviewItem(issue_id="I2", decision="正确")],
        )
        completed = _merge_review(task, merged, second)
        self.assertEqual(_review_progress(task, completed)["remaining"], 0)

    def test_batch_decision_applies_to_selected_issues(self) -> None:
        task = review_task()
        request = ReviewRequest(
            reviewer="张三",
            submit=False,
            batch_decision="正确",
            batch_issue_ids=["I1"],
            comment="批量确认",
        )
        merged = _merge_review(task, {}, request)
        self.assertEqual(len(merged["items"]), 1)
        self.assertEqual(merged["items"][0]["issue_id"], "I1")
        self.assertEqual(merged["items"][0]["decision"], "正确")


if __name__ == "__main__":
    unittest.main()

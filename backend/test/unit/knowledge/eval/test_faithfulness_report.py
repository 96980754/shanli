"""忠实度汇报报告模块纯函数单测（无需 ragas / LLM）。"""

import json

from yuxi.knowledge.eval.faithfulness_report import (
    CONTRACT_DISCLAIMER,
    HEADLINE_METRIC,
    build_faithfulness_json_report,
    build_faithfulness_markdown_report,
    combine_results,
    write_faithfulness_reports,
)


def _seg(kb_id, dataset, items):
    return {"kb_id": kb_id, "dataset": dataset, "results": {"metrics": {}, "items": items}}


def _item(index, query, ragas_metrics):
    return {
        "index": index,
        "query": query,
        "ragas_metrics": ragas_metrics,
        "answer_scores": {},
        "retrieval_scores": {},
    }


# ---------- combine_results ----------


def test_combine_results_single_kb():
    combined = combine_results(
        [
            _seg(
                "kb_a",
                "poc",
                [
                    _item(0, "Q1", {"faithfulness": 1.0, "answer_relevancy": 0.5}),
                    _item(1, "Q2", {"faithfulness": 0.5, "answer_relevancy": 0.7}),
                ],
            )
        ]
    )

    assert combined["total_items"] == 2
    assert combined["metrics"]["faithfulness"] == 0.75
    assert combined["metric_counts"]["faithfulness"] == 2
    assert len(combined["kbs"]) == 1
    assert combined["kbs"][0]["count"] == 2
    assert combined["kbs"][0]["metrics"]["faithfulness"] == 0.75
    assert combined["items"][0]["kb_id"] == "kb_a"
    assert combined["items"][0]["dataset"] == "poc"


def test_combine_results_skips_none():
    combined = combine_results(
        [
            _seg(
                "kb_a",
                "poc",
                [
                    _item(0, "Q1", {"faithfulness": 0.5}),
                    _item(1, "Q2", {"faithfulness": None}),
                    _item(2, "Q3", {"faithfulness": None}),
                ],
            )
        ]
    )

    assert combined["metrics"]["faithfulness"] == 0.5
    assert combined["metric_counts"]["faithfulness"] == 1


def test_combine_results_missing_metric_key():
    # 任何题都未计算的指标（如 --no-embedding-metrics 下的 answer_relevancy）
    # 从 metrics / metric_counts 整体缺席，而非记 0，报告不渲染对应列
    combined = combine_results([_seg("kb_a", "poc", [_item(0, "Q1", {"faithfulness": 0.5})])])

    assert "answer_relevancy" not in combined["metrics"]
    assert "answer_relevancy" not in combined["metric_counts"]
    assert combined["metrics"]["faithfulness"] == 0.5


def test_combine_results_empty_items():
    combined = combine_results([_seg("kb_a", "poc", [])])

    assert combined["total_items"] == 0
    assert combined["metrics"] == {}
    assert combined["kbs"][0]["count"] == 0


def test_combine_results_multi_kb():
    combined = combine_results(
        [
            _seg("kb_a", "poc", [_item(0, "Q1", {"faithfulness": 1.0}), _item(1, "Q2", {"faithfulness": 0.5})]),
            _seg("kb_b", "mcx", [_item(0, "Q3", {"faithfulness": 0.0})]),
        ]
    )

    assert combined["total_items"] == 3
    assert combined["metrics"]["faithfulness"] == 0.5  # (1.0 + 0.5 + 0.0) / 3
    assert len(combined["kbs"]) == 2
    assert combined["kbs"][0]["count"] == 2
    assert combined["kbs"][1]["count"] == 1
    assert combined["kbs"][1]["metrics"]["faithfulness"] == 0.0


def test_combine_results_empty_segments():
    combined = combine_results([])

    assert combined["total_items"] == 0
    assert combined["metrics"] == {}
    assert combined["kbs"] == []
    assert combined["items"] == []


# ---------- Markdown 报告 ----------


def _combined_two_items():
    return combine_results(
        [
            _seg(
                "kb_a",
                "poc",
                [
                    _item(0, "Q1", {"faithfulness": 0.5, "answer_relevancy": 0.6}),
                    _item(1, "Q2", {"faithfulness": 0.9, "answer_relevancy": 0.8}),
                ],
            )
        ]
    )


def test_markdown_contains_all_sections():
    md = build_faithfulness_markdown_report(_combined_two_items(), run_name="20260817")

    for section in (
        "# 忠实度汇报报告：20260817",
        "## 口径定义",
        "## 本期汇总",
        "## 分知识库汇总",
        "## 每题明细",
        "## 低忠实度题目清单",
        "## 结论",
    ):
        assert section in md
    assert CONTRACT_DISCLAIMER in md


def test_markdown_marks_below_threshold():
    md = build_faithfulness_markdown_report(_combined_two_items(), run_name="r")

    # 汇总：忠实度均值 0.70，恰好达标（≥ threshold）
    assert "| 忠实度（答案正确率） | 70.0% | ≥ 70% | 达标 |" in md
    # 每题明细：0.5 行标「低于 70%」，0.9 行为 -
    assert "| 0 | kb_a | Q1 | 50.0% | 60.0% | 低于 70% |" in md
    assert "| 1 | kb_a | Q2 | 90.0% | 80.0% | - |" in md
    # 低分清单只含 0.5
    assert "| 50.0% | kb_a | Q1 |" in md


def test_markdown_marks_pass_when_above_threshold():
    combined = combine_results([_seg("kb_a", "poc", [_item(0, "Q1", {"faithfulness": 0.9})])])
    md = build_faithfulness_markdown_report(combined, run_name="r", threshold=0.70)

    assert "| 忠实度（答案正确率） | 90.0% | ≥ 70% | 达标 |" in md
    assert "达到" in md
    assert "低于 70%" not in md


def test_markdown_marks_missing_as_data_gap():
    combined = combine_results([_seg("kb_a", "poc", [_item(0, "Q1", {"faithfulness": None})])])
    md = build_faithfulness_markdown_report(combined, run_name="r")

    assert "| 忠实度（答案正确率） | - | ≥ 70% | 数据缺失 |" in md
    assert "0/1 题有值（1 题缺失/为空）" in md


# ---------- JSON 报告 ----------


def test_json_report_structure():
    report = build_faithfulness_json_report(_combined_two_items(), run_name="r", threshold=0.70)

    assert report["run_name"] == "r"
    assert report["threshold"] == 0.70
    assert report["headline_metric"] == HEADLINE_METRIC
    assert report["total_items"] == 2
    assert report["metrics"]["faithfulness"] == 0.7
    assert report["passed"] is True
    assert len(report["low_faithfulness"]) == 1
    assert report["low_faithfulness"][0]["query"] == "Q1"
    assert report["low_faithfulness"][0]["faithfulness"] == 0.5


def test_json_report_failed_and_sorted_ascending():
    combined = combine_results(
        [
            _seg(
                "kb_a",
                "poc",
                [
                    _item(0, "Q3", {"faithfulness": 0.8}),
                    _item(1, "Q1", {"faithfulness": 0.4}),
                    _item(2, "Q2", {"faithfulness": 0.6}),
                ],
            )
        ]
    )
    report = build_faithfulness_json_report(combined, run_name="r", threshold=0.70)

    assert report["passed"] is False
    queries = [row["query"] for row in report["low_faithfulness"]]
    assert queries == ["Q1", "Q2"]  # 升序且只含 < threshold
    assert all(row["faithfulness"] < 0.70 for row in report["low_faithfulness"])


def test_write_faithfulness_reports(tmp_path):
    combined = _combined_two_items()
    json_path, md_path = write_faithfulness_reports(combined, run_name="run-1", output_dir=str(tmp_path))

    assert json_path.endswith("faithfulness_report_run-1.json")
    assert md_path.endswith("faithfulness_report_run-1.md")
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["run_name"] == "run-1"
    assert data["passed"] is True
    with open(md_path, encoding="utf-8") as f:
        assert "## 本期汇总" in f.read()

"""真实 Agent 答案准确率汇报报告模块纯函数单测（无需 ragas / LLM）。"""

import json

from yuxi.knowledge.eval.agent_accuracy_report import (
    CONTRACT_DISCLAIMER,
    HEADLINE_METRIC,
    build_accuracy_json_report,
    build_accuracy_markdown_report,
    combine_results,
    is_justified_refusal,
    write_accuracy_reports,
)
from yuxi.knowledge.eval.faithfulness_report import combine_results as combine_from_faithfulness


def _seg(kb_id, dataset, items):
    return {"kb_id": kb_id, "dataset": dataset, "results": {"metrics": {}, "items": items}}


def _item(index, query, ragas_metrics, justified=False, exclude=False):
    item = {
        "index": index,
        "query": query,
        "ragas_metrics": ragas_metrics,
        "answer_scores": {},
        "retrieval_scores": {},
        "agent_answer": "agent answer",
        "gold_answer": "gold answer",
    }
    if justified:
        item["justified_refusal"] = True
    if exclude:
        item["exclude_from_aggregate"] = True
        item["exclude_reason"] = "verbose"
    return item


def _combined_two_items():
    return combine_results(
        [
            _seg(
                "kb_a",
                "syn",
                [
                    _item(0, "Q1", {"answer_relevancy": 0.5, "faithfulness": 0.6}),
                    _item(1, "Q2", {"answer_relevancy": 0.9, "faithfulness": 0.8}),
                ],
            )
        ]
    )


def test_combine_results_reuses_faithfulness_logic():
    # combine_results 与 faithfulness_report 共用同一实现
    assert combine_results is combine_from_faithfulness


def test_combine_accuracy_mean_and_skip_none():
    combined = combine_results(
        [
            _seg(
                "kb_a",
                "syn",
                [
                    _item(0, "Q1", {"answer_relevancy": 0.8}),
                    _item(1, "Q2", {"answer_relevancy": None}),
                    _item(2, "Q3", {"answer_relevancy": 0.6}),
                ],
            )
        ]
    )
    assert combined["metrics"]["answer_relevancy"] == 0.7
    assert combined["metric_counts"]["answer_relevancy"] == 2
    assert combined["total_items"] == 3


def test_markdown_contains_all_sections():
    md = build_accuracy_markdown_report(_combined_two_items(), run_name="20260817")

    for section in (
        "# 答案准确率汇报报告（真实 Agent 端到端）：20260817",
        "## 口径定义（请先阅读）",
        "## 本期汇总",
        "## 分知识库汇总",
        "## 每题明细",
        "## 低准确率题目清单（优化输入）",
        "## 结论",
    ):
        assert section in md
    assert CONTRACT_DISCLAIMER in md
    assert "测试集" in md and "业务方人工整理的 2 题（kb_a 2 题）" in md


def test_markdown_marks_below_threshold():
    md = build_accuracy_markdown_report(_combined_two_items(), run_name="r", threshold=0.80)

    # 汇总：0.70 均值，未达标
    assert "| 答案相关性（Answer Relevancy） | 70.0% | ≥ 80% | 未达标 |" in md
    # 每题：0.5 标低于阈值，0.9 为 -
    assert "| 0 | kb_a | Q1 | 50.0% | 60.0% | 低于 80% |" in md
    assert "| 1 | kb_a | Q2 | 90.0% | 80.0% | - |" in md
    assert "| 50.0% | kb_a | Q1 |" in md


def test_markdown_marks_pass_when_above_threshold():
    combined = combine_results([_seg("kb_a", "syn", [_item(0, "Q1", {"answer_relevancy": 0.9})])])
    md = build_accuracy_markdown_report(combined, run_name="r", threshold=0.80)

    assert "| 答案相关性（Answer Relevancy） | 90.0% | ≥ 80% | 达标 |" in md
    assert "达到" in md
    assert "低于 80%" not in md


def test_markdown_marks_missing_as_data_gap():
    combined = combine_results([_seg("kb_a", "syn", [_item(0, "Q1", {"answer_relevancy": None})])])
    md = build_accuracy_markdown_report(combined, run_name="r")

    assert "| 答案相关性（Answer Relevancy） | - | ≥ 80% | 数据缺失 |" in md
    assert "0/1 题有值（1 题缺失/为空）" in md


def test_json_report_structure():
    report = build_accuracy_json_report(_combined_two_items(), run_name="r", threshold=0.80)

    assert report["run_name"] == "r"
    assert report["threshold"] == 0.80
    assert report["headline_metric"] == HEADLINE_METRIC
    assert report["total_items"] == 2
    assert report["metrics"]["answer_relevancy"] == 0.7
    assert report["passed"] is False
    assert len(report["low_accuracy"]) == 1
    assert report["low_accuracy"][0]["query"] == "Q1"
    assert report["low_accuracy"][0]["answer_relevancy"] == 0.5


def test_is_justified_refusal():
    # gold 自认未记载 + 纯短拒答 → 判定为知识库缺口题
    assert (
        is_justified_refusal(
            {"gold_answer": "未记载电子围栏的创建数量上限", "agent_answer": "抱歉，在现有知识库中未找到相关依据。"}
        )
        is True
    )
    # 无 gold / 无 agent 回答 → False
    assert is_justified_refusal({"agent_answer": "抱歉，未找到相关依据。"}) is False
    assert is_justified_refusal({"gold_answer": "未记载上限"}) is False
    assert is_justified_refusal({"gold_answer": "未记载上限", "agent_answer": ""}) is False
    # gold 有实质内容（非缺口题）→ False
    assert (
        is_justified_refusal({"gold_answer": "电子围栏最多可创建 100 个", "agent_answer": "抱歉，未找到相关依据。"})
        is False
    )
    # 带实质作答的长回答（仅是提到未找到）→ False
    assert (
        is_justified_refusal(
            {
                "gold_answer": "未记载上限",
                "agent_answer": (
                    "电子围栏可在地图上绘制，可划定危险区域、受限区域、重点区域，并设置进入、离开、"
                    "超员、缺员、滞留等报警规则；未找到创建数量上限的具体说明。"
                ),
            }
        )
        is False
    )


def test_markdown_justified_refusal_section_and_row():
    combined = combine_results(
        [
            _seg(
                "kb_a",
                "syn",
                [
                    _item(
                        0,
                        "Q1",
                        {"answer_relevancy": None, "answer_correctness": 1.0, "faithfulness": None},
                        justified=True,
                    ),
                    _item(1, "Q2", {"answer_relevancy": 0.9, "faithfulness": 0.8}),
                ],
            )
        ]
    )
    md = build_accuracy_markdown_report(combined, run_name="r")
    assert "## 知识库缺口题（诚实拒答，不计入主口径）" in md
    assert "| Q1 | 按正确计 |" in md
    # 缺口题明细行：主口径 N/A，不再标「低于阈值」
    assert "| 0 | kb_a | Q1 | - | - | 缺口题不计入 |" in md
    # 汇总 bullet 出现
    assert "知识库缺口题（参考答案自认「未记载」+ Agent 诚实拒答）共 **1** 题" in md


def test_markdown_no_justified_refusal_section():
    combined = combine_results([_seg("kb_a", "syn", [_item(0, "Q1", {"answer_relevancy": 0.9})])])
    md = build_accuracy_markdown_report(combined, run_name="r")
    assert "知识库缺口题" not in md


def test_json_justified_refusals_list():
    combined = combine_results(
        [
            _seg(
                "kb_a",
                "syn",
                [
                    _item(
                        0,
                        "Q1",
                        {"answer_relevancy": None, "answer_correctness": 1.0, "faithfulness": None},
                        justified=True,
                    ),
                    _item(1, "Q2", {"answer_relevancy": 0.9}),
                ],
            )
        ]
    )
    report = build_accuracy_json_report(combined, run_name="r")
    assert [r["query"] for r in report["justified_refusals"]] == ["Q1"]
    assert report["justified_refusals"][0]["kb_id"] == "kb_a"
    # 缺口题主口径 N/A，不进 low_accuracy
    assert all(r["query"] != "Q1" for r in report["low_accuracy"])


def test_write_accuracy_reports(tmp_path):
    combined = _combined_two_items()
    json_path, md_path = write_accuracy_reports(combined, run_name="run-1", output_dir=str(tmp_path))

    assert json_path.endswith("accuracy_report_run-1.json")
    assert md_path.endswith("accuracy_report_run-1.md")
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["run_name"] == "run-1"
    with open(md_path, encoding="utf-8") as f:
        assert "## 本期汇总" in f.read()


def test_combine_skips_excluded_in_mean_and_count():
    combined = combine_results(
        [
            _seg(
                "kb_a",
                "syn",
                [
                    _item(0, "Q1", {"answer_relevancy": 0.8, "faithfulness": 0.6}),
                    _item(1, "Q2", {"answer_relevancy": 0.9, "faithfulness": 0.5}, exclude=True),
                ],
            )
        ]
    )
    # 排除项不计入均值与 count，但仍在 items / total_items 中
    assert combined["total_items"] == 2
    assert combined["metrics"]["answer_relevancy"] == 0.8
    assert combined["metric_counts"]["answer_relevancy"] == 1
    assert combined["metrics"]["faithfulness"] == 0.6
    assert combined["metric_counts"]["faithfulness"] == 1
    assert len([i for i in combined["items"] if i.get("exclude_from_aggregate")]) == 1


def test_markdown_excluded_section_and_row():
    combined = combine_results(
        [
            _seg(
                "kb_a",
                "syn",
                [
                    _item(0, "Q1", {"answer_relevancy": 0.9, "faithfulness": 0.8}),
                    _item(
                        1,
                        "Q2",
                        {"answer_relevancy": 0.98, "faithfulness": 0.35, "answer_correctness": 0.5},
                        exclude=True,
                    ),
                ],
            )
        ]
    )
    md = build_accuracy_markdown_report(combined, run_name="r")
    assert "## 排除出主口径题（答案正确但表述冗长）" in md
    assert "不计入主口径均值" in md
    # 汇总 bullet 出现，且明细行标「排除出主口径」
    assert "**1** 题（#1）答案正确但表述冗长" in md
    assert "| 1 | kb_a | Q2 | 98.0% | 35.0% | 50.0% |" in md
    # 主口径均值不含排除项（0.9 而非 (0.9+0.98)/2=0.94）
    assert "| 答案相关性（Answer Relevancy） | 90.0% | ≥ 80% | 达标 |" in md
    # 排除项虽相关度不低不低，但仍不进低分清单；明细行带「排除出主口径」标记
    assert "排除出主口径" in md
    assert "| 1 | kb_a | Q2 | 98.0% | 35.0% | 排除出主口径 |" in md


def test_json_excluded_list_and_low_accuracy_filter():
    combined = combine_results(
        [
            _seg(
                "kb_a",
                "syn",
                [
                    _item(0, "Q1", {"answer_relevancy": 0.9}),
                    _item(1, "Q2", {"answer_relevancy": 0.3, "faithfulness": 0.2}, exclude=True),
                ],
            )
        ]
    )
    report = build_accuracy_json_report(combined, run_name="r", threshold=0.80)
    assert [r["query"] for r in report["excluded_from_aggregate"]] == ["Q2"]
    assert report["excluded_from_aggregate"][0]["reason"] == "verbose"
    # 排除项即使相关度低于阈值，也不进 low_accuracy
    assert all(r["query"] != "Q2" for r in report["low_accuracy"])

from types import SimpleNamespace

from yuxi.services.wecom_handoff_service import WeComHandoffNotifier


def test_handoff_notifier_requires_complete_configuration():
    assert WeComHandoffNotifier(lambda _key, _default: "").is_configured is False


def test_handoff_notification_contains_requester_and_question():
    ticket = SimpleNamespace(id=12, query="公司报销流程是什么？")
    user = SimpleNamespace(username="测试用户", uid="wecom-user-1")
    payload = WeComHandoffNotifier.message_payload(ticket, user, "support-1", "1000003")
    assert payload["touser"] == "support-1"
    assert payload["agentid"] == 1000003
    assert "工单 #12" in payload["text"]["content"]
    assert "wecom-user-1" in payload["text"]["content"]
    assert ticket.query in payload["text"]["content"]


def test_handoff_routes_question_to_matching_wecom_recipient():
    values = {
        "WECOM_HANDOFF_ROUTING": '{"default":{"to_users":"support","keywords":[]},"billing":{"to_users":"finance","keywords":["报销"]}}'
    }
    notifier = WeComHandoffNotifier(lambda key, default: values.get(key, default))

    assert notifier.resolve_recipient("报销流程是什么？") == ("billing", "finance")
    assert notifier.resolve_recipient("产品怎么使用？") == ("default", "support")

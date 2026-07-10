from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


ADMIN_JS_PATH = Path(__file__).resolve().parents[1] / "app" / "static" / "admin.js"
QA_JS_PATH = Path(__file__).resolve().parents[1] / "app" / "static" / "qa.js"


def test_login_page_is_served():
    client = TestClient(create_app())

    response = client.get("/login")

    assert response.status_code == 200
    assert "登录" in response.text


def test_admin_shell_is_served():
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert "知识库管理台" in response.text


def test_admin_shell_contains_kb_document_permission_and_delete_regions():
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'id="kb-list"' in response.text
    assert 'id="document-list"' in response.text
    assert 'id="permission-list"' in response.text
    assert 'id="delete-panel"' in response.text


def test_admin_shell_contains_permission_editor_and_feedback_regions():
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'id="permission-editor"' in response.text
    assert 'id="permission-user-id"' in response.text
    assert 'id="save-permission"' in response.text
    assert 'id="delete-message"' in response.text


def test_admin_shell_contains_document_and_kb_action_controls():
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'id="confirm-delete"' in response.text
    assert 'id="cancel-delete"' in response.text
    assert 'id="delete-target"' in response.text


def test_admin_shell_contains_workspace_message_and_kb_delete_button():
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'id="admin-message"' in response.text
    assert 'id="delete-kb-button"' in response.text


def test_admin_shell_contains_kb_create_form():
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'id="kb-create-form"' in response.text
    assert 'id="kb-create-name"' in response.text
    assert 'id="kb-create-description"' in response.text
    assert 'id="kb-create-visibility"' in response.text
    assert 'id="kb-create-submit"' in response.text


def test_admin_shell_contains_kb_create_hooks():
    admin_js = ADMIN_JS_PATH.read_text(encoding="utf-8")

    assert "handleKnowledgeBaseCreate" in admin_js
    assert "kb-create-form" in admin_js
    assert "refreshKnowledgeBases()" in admin_js


def test_admin_and_qa_pages_contain_navigation_links():
    client = TestClient(create_app())

    admin_response = client.get("/admin")
    qa_response = client.get("/qa")

    assert admin_response.status_code == 200
    assert qa_response.status_code == 200
    assert 'href="/qa"' in admin_response.text
    assert 'href="/admin"' in qa_response.text


def test_admin_and_qa_pages_contain_empty_state_markers():
    client = TestClient(create_app())

    admin_response = client.get("/admin")
    qa_response = client.get("/qa")

    assert admin_response.status_code == 200
    assert qa_response.status_code == 200
    assert 'id="kb-empty-state"' in admin_response.text
    assert 'id="document-empty-state"' in admin_response.text
    assert 'id="permission-empty-state"' in admin_response.text
    assert 'id="issue-empty-state"' in admin_response.text
    assert 'id="qa-kb-empty-state"' in qa_response.text


def test_admin_shell_contains_upload_and_document_detail_regions():
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'id="document-upload-form"' in response.text
    assert 'id="document-file"' in response.text
    assert 'id="upload-document"' in response.text
    assert 'id="document-detail"' in response.text
    assert 'id="document-detail-title"' in response.text
    assert 'id="document-detail-status"' in response.text
    assert 'id="document-detail-block-count"' in response.text
    assert 'id="document-detail-chunk-count"' in response.text


def test_admin_shell_contains_upload_feedback_and_selection_state_hooks():
    client = TestClient(create_app())

    response = client.get("/admin")
    admin_js = ADMIN_JS_PATH.read_text(encoding="utf-8")

    assert response.status_code == 200
    assert 'id="admin-message"' in response.text
    assert 'id="document-detail-title"' in response.text
    assert 'id="document-detail-type"' in response.text
    assert "let selectedDocumentId = null" in admin_js
    assert "let selectedDocumentDetail = null" in admin_js
    assert "let uploading = false" in admin_js
    assert "document-upload-form" in admin_js


def test_admin_shell_contains_document_detail_fields_and_delete_feedback():
    client = TestClient(create_app())

    response = client.get("/admin")
    admin_js = ADMIN_JS_PATH.read_text(encoding="utf-8")

    assert response.status_code == 200
    assert 'id="document-detail-status"' in response.text
    assert 'id="document-detail-block-count"' in response.text
    assert 'id="document-detail-chunk-count"' in response.text
    assert 'id="delete-message"' in response.text
    assert "renderDocumentDetail(null)" in admin_js
    assert "void loadDocumentDetail(item.id)" in admin_js


def test_admin_shell_contains_document_metadata_detail_fields():
    client = TestClient(create_app())
    response = client.get("/admin")
    admin_js = ADMIN_JS_PATH.read_text(encoding="utf-8")

    assert response.status_code == 200
    assert 'id="document-detail-department"' in response.text
    assert 'id="document-detail-product-line"' in response.text
    assert 'id="document-detail-visibility"' in response.text
    assert 'id="document-detail-security-level"' in response.text
    assert 'id="document-detail-tags"' in response.text
    assert "document-detail-department" in admin_js
    assert "document-detail-product-line" in admin_js
    assert "document-detail-visibility" in admin_js
    assert "document-detail-security-level" in admin_js
    assert "document-detail-tags" in admin_js


def test_admin_shell_contains_issue_list_region_and_issue_hooks():
    client = TestClient(create_app())

    response = client.get("/admin")
    admin_js = ADMIN_JS_PATH.read_text(encoding="utf-8")

    assert response.status_code == 200
    assert 'id="issue-list"' in response.text
    assert "let issues = []" in admin_js
    assert "loadIssues" in admin_js
    assert "updateIssueStatus" in admin_js


def test_qa_page_is_served():
    client = TestClient(create_app())

    response = client.get("/qa")

    assert response.status_code == 200
    assert "知识库问答" in response.text


def test_qa_page_contains_minimal_query_regions():
    client = TestClient(create_app())

    response = client.get("/qa")

    assert response.status_code == 200
    assert 'id="qa-user"' in response.text
    assert 'id="qa-kb-select"' in response.text
    assert 'id="qa-question"' in response.text
    assert 'id="qa-submit"' in response.text
    assert 'id="qa-answer-content"' in response.text
    assert 'id="qa-sources"' in response.text
    assert 'id="qa-feedback-helpful"' in response.text
    assert 'id="qa-feedback-unhelpful"' in response.text
    assert 'id="qa-message"' in response.text


def test_qa_page_contains_conversation_history_regions():
    client = TestClient(create_app())

    response = client.get("/qa")

    assert response.status_code == 200
    assert 'id="qa-conversation-list"' in response.text
    assert 'id="qa-message-list"' in response.text


def test_qa_static_js_contains_auth_and_kb_loading_hooks():
    client = TestClient(create_app())

    response = client.get("/static/qa.js")
    qa_js = QA_JS_PATH.read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "let authProfile = null" in qa_js
    assert "let knowledgeBases = []" in qa_js
    assert "let activeKbId = null" in qa_js
    assert "loadQaShell" in qa_js
    assert "renderKnowledgeBases" in qa_js


def test_qa_static_js_contains_conversation_history_hooks():
    qa_js = QA_JS_PATH.read_text(encoding="utf-8")

    assert "let conversations = []" in qa_js
    assert "loadConversations" in qa_js
    assert "renderConversations" in qa_js
    assert "/api/qa/conversations?kb_id=" in qa_js


def test_qa_static_js_contains_conversation_message_hooks():
    qa_js = QA_JS_PATH.read_text(encoding="utf-8")

    assert "let messages = []" in qa_js
    assert "loadConversationMessages" in qa_js
    assert "renderMessages" in qa_js
    assert "/api/qa/conversations/${conversationId}/messages" in qa_js


def test_qa_static_js_refreshes_history_after_question():
    qa_js = QA_JS_PATH.read_text(encoding="utf-8")

    assert "await loadConversations()" in qa_js
    assert "await loadConversationMessages(activeConversationId)" in qa_js


def test_qa_static_js_contains_question_submission_hooks():
    qa_js = QA_JS_PATH.read_text(encoding="utf-8")

    assert "let activeConversationId = null" in qa_js
    assert "let activeMessageId = null" in qa_js
    assert "let asking = false" in qa_js
    assert "submitQuestion" in qa_js
    assert "renderAnswer" in qa_js
    assert "renderSources" in qa_js
    assert "/api/qa/ask/sync" in qa_js


def test_qa_static_js_contains_feedback_hooks():
    qa_js = QA_JS_PATH.read_text(encoding="utf-8")

    assert "submitFeedback" in qa_js
    assert "/api/qa/feedback" in qa_js
    assert "qa-feedback-helpful" in qa_js
    assert "qa-feedback-unhelpful" in qa_js

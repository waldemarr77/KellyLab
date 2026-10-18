import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
import main
from main import app

client = TestClient(app)

SAMPLE_PAYLOAD = {
    "format": "pdf",
    "title": "Python Junior Співбесіда",
    "questions": [
        {"order": 1, "text": "Що таке ORM?", "answer": "ORM — це об'єктно-реляційне відображення."},
        {"order": 2, "text": "Що таке транзакція?", "answer": ""},
    ],
}


def test_export_pdf_success():
    payload = dict(SAMPLE_PAYLOAD, format="pdf")
    response = client.post("/export", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 1000


def test_export_docx_success():
    payload = dict(SAMPLE_PAYLOAD, format="docx")
    response = client.post("/export", json=payload)
    assert response.status_code == 200
    assert (
        response.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert response.content.startswith(b"PK")
    assert len(response.content) > 1000


def test_export_invalid_format_returns_422():
    payload = dict(SAMPLE_PAYLOAD, format="txt")
    response = client.post("/export", json=payload)
    assert response.status_code == 422
    assert "detail" in response.json()


def test_export_case_insensitive_format():
    payload = dict(SAMPLE_PAYLOAD, format="PDF")
    response = client.post("/export", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"


def test_export_too_many_questions_returns_422():
    questions = [{"order": i, "text": f"Питання {i}", "answer": ""} for i in range(1, 52)]
    payload = dict(SAMPLE_PAYLOAD, questions=questions)
    response = client.post("/export", json=payload)
    assert response.status_code == 422


def test_export_question_text_too_long_returns_422():
    questions = [{"order": 1, "text": "A" * 2001, "answer": ""}]
    payload = dict(SAMPLE_PAYLOAD, questions=questions)
    response = client.post("/export", json=payload)
    assert response.status_code == 422


def test_export_empty_title_returns_422():
    payload = dict(SAMPLE_PAYLOAD, title="")
    response = client.post("/export", json=payload)
    assert response.status_code == 422


def test_export_special_characters_escaping():
    payload = {
        "format": "pdf",
        "title": "Тест & Символи < > \" '",
        "questions": [
            {
                "order": 1,
                "text": "Чи правда, що 2 < 3 & 4 > 1?",
                "answer": "Так, бо 2 < 3 та 4 > 1.",
            }
        ],
    }
    response = client.post("/export", json=payload)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_export_empty_questions():
    payload = {
        "format": "docx",
        "title": "Порожній список питань",
        "questions": [],
    }
    response = client.post("/export", json=payload)
    assert response.status_code == 200
    assert response.content.startswith(b"PK")


def test_export_internal_server_error_hides_stack_trace(monkeypatch):
    def broken_pdf(*args, **kwargs):
        raise RuntimeError("Secret internal database/generator exception")

    monkeypatch.setattr(main, "generate_pdf", broken_pdf)
    payload = dict(SAMPLE_PAYLOAD, format="pdf")
    response = client.post("/export", json=payload)

    assert response.status_code == 500
    data = response.json()
    assert data["detail"] == "Внутрішня помилка сервера"
    assert "Secret internal" not in response.text
    assert "Traceback" not in response.text


def test_export_oversized_request_is_rejected():
    huge_headers = {"content-length": str(main.MAX_REQUEST_SIZE + 1000)}
    response = client.post("/export", content=b"{}", headers=huge_headers)
    assert response.status_code == 413


def test_health_check_returns_200_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_export_answer_too_long_returns_422():
    questions = [{"order": 1, "text": "Питання", "answer": "A" * 10001}]
    payload = dict(SAMPLE_PAYLOAD, questions=questions)
    response = client.post("/export", json=payload)
    assert response.status_code == 422


def test_questions_are_sorted_by_order_in_export():
    # Передаємо питання не за порядком: спочатку 2, потім 1
    unordered = [
        {"order": 2, "text": "Друге питання", "answer": ""},
        {"order": 1, "text": "Перше питання", "answer": ""},
    ]
    payload = dict(SAMPLE_PAYLOAD, format="docx", questions=unordered)
    response = client.post("/export", json=payload)
    assert response.status_code == 200

    # Перевіряємо порядок у згенерованому docx
    import io, docx
    doc = docx.Document(io.BytesIO(response.content))
    paragraphs = [p.text for p in doc.paragraphs if p.text]
    assert paragraphs[1].startswith("1. Перше питання")
    assert paragraphs[2].startswith("2. Друге питання")


def test_long_unbroken_string_in_pdf_does_not_crash():
    long_url = "https://example.com/very/long/path/" + "abcdefghij" * 20
    payload = {
        "format": "pdf",
        "title": "Довгий URL",
        "questions": [{"order": 1, "text": long_url, "answer": long_url}],
    }
    response = client.post("/export", json=payload)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_fail_fast_when_font_missing(monkeypatch):
    monkeypatch.setattr(main, "font_registered", False)
    payload = dict(SAMPLE_PAYLOAD, format="pdf")
    response = client.post("/export", json=payload)
    assert response.status_code == 500
    assert "шрифт із кирилицею недоступний" in response.json()["detail"]

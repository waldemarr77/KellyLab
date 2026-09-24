import io
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_corrupted_pdf_returns_400():
    """Пошкоджений PDF файл має повертати 400, а не 500."""
    content = b"this is not a real pdf file"
    response = client.post(
        "/parse",
        files={"file": ("broken.pdf", io.BytesIO(content), "application/pdf")}
    )
    assert response.status_code == 400

def test_normal_questions_list():
    """Тест 1: звичайний список пронумерованих питань у TXT файлі."""
    content = (
        "1. Що таке Python?\n"
        "2. Розкажи про типи даних.\n"
        "3. Чим list відрізняється від tuple?\n"
    ).encode("utf-8")

    response = client.post(
        "/parse",
        files={"file": ("questions.txt", io.BytesIO(content), "text/plain")}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["questions"] == [
    "Що таке Python?",
    "Розкажи про типи даних.",
    "Чим list відрізняється від tuple?",
    ]


def test_technical_names_with_dots():
    """Тест 2: питання з технічними назвами, що містять крапки (Node.js, asyncio.sleep())."""
    content = (
        "1. Поясни різницю між Promise та async/await в Node.js?\n"
        "2. Що робить функція asyncio.sleep()?\n"
    ).encode("utf-8")

    response = client.post(
        "/parse",
        files={"file": ("tech.txt", io.BytesIO(content), "text/plain")}
    )

    assert response.status_code == 200
    data = response.json()
    assert "Поясни різницю між Promise та async/await в Node.js?" in data["questions"]
    assert "Що робить функція asyncio.sleep()?" in data["questions"]


def test_sentences_without_question_mark_are_excluded():
    """Тест 3: звичайні речення без знаку питання не повинні потрапляти у результат."""
    content = (
        "Це просто вступний текст без питання.\n"
        "Python - це мова програмування.\n"
        "1. А це вже реальне питання?\n"
    ).encode("utf-8")

    response = client.post(
        "/parse",
        files={"file": ("mixed.txt", io.BytesIO(content), "text/plain")}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["questions"] == ["А це вже реальне питання?"]
    for q in data["questions"]:
        assert "вступний текст" not in q
        assert "мова програмування" not in q


def test_uppercase_extension_is_accepted():
    """Тест 4: розширення файлу у верхньому регістрі (.TXT) має оброблятись так само, як .txt."""
    content = "1. Чи працює файл з розширенням у верхньому регістрі?\n".encode("utf-8")

    response = client.post(
        "/parse",
        files={"file": ("questions.TXT", io.BytesIO(content), "text/plain")}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["questions"] == ["Чи працює файл з розширенням у верхньому регістрі?"]


def test_invalid_format_rejected():
    """Додатково: невірний формат файлу повертає 400."""
    content = b"just some content"

    response = client.post(
        "/parse",
        files={"file": ("virus.exe", io.BytesIO(content), "application/octet-stream")}
    )

    assert response.status_code == 400


def test_empty_file_rejected():
    """Додатково: порожній файл повертає 400."""
    response = client.post(
        "/parse",
        files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")}
    )

    assert response.status_code == 400


def test_file_too_large_rejected():
    """Додатково: файл більше 10MB повертає 413."""
    big_content = ("Що таке Python? " * 1_000_000).encode("utf-8")

    response = client.post(
        "/parse",
        files={"file": ("big.txt", io.BytesIO(big_content), "text/plain")}
    )

    assert response.status_code == 413
import html
import io
import logging
import os
import re
import time
from urllib.parse import quote

import docx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="KellyLab Exporter Service")

EXPORT_TIME_LIMIT = 60  # максимум 60 секунд на генерацію
MAX_REQUEST_SIZE = 2 * 1024 * 1024  # 2 MB ліміт на розмір вхідного JSON

# Пошук шрифту з кирилицею для ReportLab
CANDIDATE_FONTS = [
    os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf"),
    os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arial.ttf"),
    os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "calibri.ttf"),
    os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "segoeui.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/opentype/dejavu/DejaVuSans.ttf",
]

FONT_NAME = "AppCyrillicFont"
font_registered = False

for font_path in CANDIDATE_FONTS:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont(FONT_NAME, font_path))
            font_registered = True
            logger.info("Успішно зареєстровано шрифт для PDF: %s", font_path)
            break
        except Exception as exc:
            logger.warning("Не вдалося завантажити шрифт %s: %s", font_path, exc)


def check_font_available() -> None:
    """Fail-fast: якщо шрифт не знайдено, не створюємо пошкоджений файл."""
    if not font_registered:
        raise HTTPException(
            status_code=500,
            detail="Внутрішня помилка сервера: шрифт із кирилицею недоступний",
        )


class QuestionItem(BaseModel):
    order: int
    text: str = Field(..., min_length=1, max_length=2000, description="Текст питання (до 2000 символів)")
    answer: str = Field(default="", max_length=10000, description="Відповідь на питання (до 10000 символів)")


class ExportRequest(BaseModel):
    format: str = Field(..., description="Формат файлу: 'pdf' або 'docx'")
    title: str = Field(..., min_length=1, max_length=255, description="Назва документа")
    questions: list[QuestionItem] = Field(
        default_factory=list,
        max_length=50,
        description="Список питань (до 50 елементів)",
    )

    @field_validator("format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in ("pdf", "docx"):
            raise ValueError(f"Непідтримуваний формат: {v!r}. Дозволені: 'pdf', 'docx'")
        return cleaned


def generate_docx(title: str, questions: list[QuestionItem]) -> bytes:
    """Генерація DOCX документа з підтримкою кирилиці."""
    doc = docx.Document()
    doc.add_heading(title, level=1)
    doc.add_paragraph("")

    for item in questions:
        p_q = doc.add_paragraph()
        run_q = p_q.add_run(f"{item.order}. {item.text}")
        run_q.bold = True

        if item.answer.strip():
            p_a = doc.add_paragraph()
            p_a.paragraph_format.left_indent = docx.shared.Pt(18)
            p_a.add_run("Відповідь: ").italic = True
            p_a.add_run(item.answer)

        doc.add_paragraph("")  # відступ між питаннями

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def generate_pdf(title: str, questions: list[QuestionItem]) -> bytes:
    """Генерація PDF документа з підтримкою кирилиці через ReportLab."""
    check_font_available()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=40,
        bottomMargin=40,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "PDFTitle",
        parent=styles["Heading1"],
        fontName=FONT_NAME,
        fontSize=18,
        leading=22,
        spaceAfter=14,
        wordWrap="CJK",
    )
    question_style = ParagraphStyle(
        "PDFQuestion",
        parent=styles["Normal"],
        fontName=FONT_NAME,
        fontSize=12,
        leading=16,
        spaceAfter=6,
        wordWrap="CJK",
    )
    answer_style = ParagraphStyle(
        "PDFAnswer",
        parent=styles["Normal"],
        fontName=FONT_NAME,
        fontSize=10,
        leading=14,
        leftIndent=15,
        spaceAfter=12,
        textColor="#333333",
        wordWrap="CJK",
    )

    story = [
        Paragraph(html.escape(title), title_style),
        Spacer(1, 10),
    ]

    for item in questions:
        q_text = f"<b>{item.order}.</b> {html.escape(item.text)}"
        story.append(Paragraph(q_text, question_style))

        if item.answer.strip():
            a_text = f"<i>Відповідь:</i> {html.escape(item.answer)}"
            story.append(Paragraph(a_text, answer_style))
        else:
            story.append(Spacer(1, 8))

    doc.build(story)
    return buffer.getvalue()


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    """Обмежує розмір вхідного запиту для запобігання переповнення пам'яті."""
    if request.method == "POST":
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_REQUEST_SIZE:
            return JSONResponse(status_code=413, content={"detail": "Розмір запиту перевищує ліміт"})
    return await call_next(request)


@app.get("/health")
def health_check():
    """Ендпоінт для перевірки готовності сервісу (healthcheck для Docker / Compose)."""
    return {"status": "ok"}


@app.post("/export")
def export_file(request: ExportRequest):
    start = time.perf_counter()
    file_format = request.format
    # Гарантуємо правильний порядок питань
    sorted_questions = sorted(request.questions, key=lambda q: q.order)

    try:
        if file_format == "pdf":
            content = generate_pdf(request.title, sorted_questions)
            media_type = "application/pdf"
            extension = "pdf"
        else:
            content = generate_docx(request.title, sorted_questions)
            media_type = (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            extension = "docx"

        duration = time.perf_counter() - start
        if duration > EXPORT_TIME_LIMIT:
            raise HTTPException(status_code=504, detail="Експорт перевищив ліміт часу")

        # Безпечне та компактне ім'я файлу (до 60 символів, без службових символів)
        clean_title = (
            re.sub(r'[\\/*?:"<>|\r\n\t]', "_", request.title.strip())[:60].strip()
            or "export"
        )
        safe_filename = quote(f"{clean_title}.{extension}")
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}",
        }

        logger.info(
            "code=200 format=%s title=%s duration=%.2f",
            file_format,
            request.title,
            duration,
        )
        return Response(content=content, media_type=media_type, headers=headers)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("code=500 format=%s error=%s", file_format, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Внутрішня помилка сервера") from exc


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)

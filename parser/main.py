import io
import logging
import re
import time
import zipfile

import docx
import pdfplumber
import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

app = FastAPI()

MAX_FILE_SIZE = 10_485_760

async def parser_pdf(file):
    content = await file.read()
    text = ""

    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()

                if page_text:
                    text += page_text + "\n"

    except (
        pdfplumber.utils.exceptions.PdfminerException,
        pdfplumber.utils.exceptions.MalformedPDFException
    ):
        raise HTTPException(
            status_code=400,
            detail="Не вдалося прочитати PDF файл, можливо він пошкоджений"
        )

    return text


async def parser_docx(file):
    content = await file.read()
    text = ""

    try:
        doc = docx.Document(io.BytesIO(content))

        for paragraph in doc.paragraphs:
            text += paragraph.text + "\n"

        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    text += cell.text + "\n"

    except (zipfile.BadZipFile, KeyError):
        raise HTTPException(
            status_code=400,
            detail="Не вдалося прочитати DOCX файл, можливо він пошкоджений"
        )

    return text


async def parser_txt(file):
    content = await file.read()

    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1251")


async def tru_format(file_format, file):
    if file_format == "pdf":
        return await parser_pdf(file)
    elif file_format == "docx":
        return await parser_docx(file)
    elif file_format == "txt":
        return await parser_txt(file)


def find_quetion(text):
    if not text or not text.strip():
        return []

    question_starters = (
        "що ", "як ", "чому ", "де ", "коли ", "хто ", "який ", "яка ", "яке ",
        "поясни ", "розкажи ", "опиши ", "назві ", "в чому ", "чим ", "для чого ",
        "навіщо ", "чи ", "чи можна ", "в чому різниця", "в чому відмінність",
        "what ", "how ", "why ", "where ", "when ", "who ", "which ",
        "explain ", "describe ", "tell ", "name ", "define ", "list ",
        "can you ", "could you ", "is there ", "are there ", "does ", "do ",
        "difference between ", "what is ", "what are ", "how does ", "how do "
    )

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    questions = []

    for line in lines:
        lower = line.lower()
        is_question = line.endswith("?")
        starts_with_number = bool(re.match(r"^[\(\[]?\d+[\)\].\-\:]\s*", line))
        starts_with_question_word = any(
            lower.startswith(starter)
            for starter in question_starters
        )

        if is_question or starts_with_number or starts_with_question_word:
            cleaned = re.sub(
                r"^[\(\[]?\d+[\)\].\-\:]\s*",
                "",
                line
            ).strip()

            if cleaned:
                questions.append(cleaned)

    seen = set()
    unique = []

    for q in questions:
        if q not in seen:
            seen.add(q)
            unique.append(q)

    return unique


@app.post("/parse")
async def parser_file(file: UploadFile):
    start = time.time()

    try:
        file_format = file.filename.split(".")[-1].lower()
        file_size = 0

        if file_format in ("txt", "docx", "pdf"):
            while chunk := await file.read(1024 * 1024):
                file_size += len(chunk)

                if file_size > MAX_FILE_SIZE:
                    duration = time.time() - start
                    logger.error(
                        f"ERROR|code:413|{file.filename}|{duration:.2f}"
                    )
                    raise HTTPException(
                        status_code=413,
                        detail="Файл завеликий"
                    )

            if file_size > 0:
                file.file.seek(0)

                text = await tru_format(file_format, file)
                questions = find_quetion(text)

                duration = time.time() - start
                logger.info(
                    f"code:200|{file.filename}|{duration:.2f}"
                )

                return {
                    "filename": file.filename,
                    "questions": questions
                }

            duration = time.time() - start
            logger.error(
                f"code:400|{file.filename}|{duration:.2f}"
            )
            raise HTTPException(
                status_code=400,
                detail="Файл порожній"
            )

        duration = time.time() - start
        logger.error(
            f"code:400|{file.filename}|{duration:.2f}"
        )
        raise HTTPException(
            status_code=400,
            detail="Невірний формат файлу"
        )

    except HTTPException:
        raise

    except Exception as e:
        duration = time.time() - start
        logger.error(
            f"code:500|{file.filename}|{e!s}"
        )
        raise HTTPException(
            status_code=500,
            detail="Внутрішня помилка сервера"
        )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
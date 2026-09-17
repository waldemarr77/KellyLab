from fastapi import FastAPI, UploadFile, HTTPException
import uvicorn
import logging
import time
import re
import io
import pdfplumber
import docx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

app = FastAPI()


async def parser_pdf(file):
    content = await file.read()
    text = ""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text


async def parser_docx(file):
    content = await file.read()
    doc = docx.Document(io.BytesIO(content))
    text = ""
    for paragraph in doc.paragraphs:
        text += paragraph.text + "\n"
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
    normalized = re.sub(r'\s+', ' ', text)
    questions = re.findall(r'[^.!?]*\?', normalized)
    questions = [q.strip() for q in questions if q.strip()]
    questions = [re.sub(r'^\d+\s*[\.\)\-]?\s*', '', q) for q in questions]
    return questions


@app.post("/parse")
async def parser_file(file: UploadFile):
    try:
        start = time.time()
        file_format = file.filename.split(".")[-1]
        file_size = 0

        if file_format in ("txt", "docx", "pdf"):
            while chunk := await file.read(1024 * 1024):
                file_size += len(chunk)

                if file_size >= 10_485_760:
                    duration = time.time() - start
                    logging.error(f"ERROR|code:413|{file.filename}|{duration:.2f}")
                    raise HTTPException(status_code=413, detail="Файл завеликий")

            if file_size > 0:
                file.file.seek(0)
                text = await tru_format(file_format, file)
                questions = find_quetion(text)

                duration = time.time() - start
                logging.info(f"code:200|{file.filename}|{duration:.2f}")
                return {"filename": file.filename, "questions": questions}
            else:
                duration = time.time() - start
                logging.error(f"code:400|{file.filename}|{duration:.2f}")
                raise HTTPException(status_code=400, detail="Файл порожній")
        else:
            duration = time.time() - start
            logging.error(f"code:400|{file.filename}|{duration:.2f}")
            raise HTTPException(status_code=400, detail="Невірний формат файлу")

    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start
        logging.error(f"code:500|{file.filename}|{str(e)}")
        raise HTTPException(status_code=500, detail="Внутрішня помилка сервера")



if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
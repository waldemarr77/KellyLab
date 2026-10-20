import logging

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sandbox import run_code

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="KellyLab Runner Service")

MAX_CODE_LENGTH = 20_000
MAX_INPUT_LENGTH = 100_000
MAX_TESTS = 50
MAX_TIME_LIMIT = 10
SUPPORTED_LANGUAGES = ("python",)


class TestCase(BaseModel):
    input: str = Field(default="", max_length=MAX_INPUT_LENGTH)


class RunRequest(BaseModel):
    language: str
    code: str = Field(max_length=MAX_CODE_LENGTH)
    tests: list[TestCase] = Field(min_length=1, max_length=MAX_TESTS)
    time_limit: int = Field(default=2, ge=1, le=MAX_TIME_LIMIT)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/run")
def run(request: RunRequest):
    if request.language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Непідтримувана мова: {request.language}")
    # Тести виконуються послідовно: один запуск за раз тримає ресурси в межах лімітів контейнера.
    results = [run_code(request.code, case.input, request.time_limit) for case in request.tests]
    statuses = [result["status"] for result in results]
    logger.info("code=200 tests=%d statuses=%s", len(results), ",".join(statuses))
    return {"results": results}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8003)

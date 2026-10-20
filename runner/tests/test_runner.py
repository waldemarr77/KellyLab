import sys

import pytest

# Пісочниця тримається на resource.setrlimit, fork і setuid — це лише Linux (і сам контейнер runner).
if sys.platform != "linux":
    pytest.skip("Пісочниця працює лише на Linux", allow_module_level=True)

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def run(code, tests=None, time_limit=2):
    body = {"language": "python", "code": code, "tests": tests or [{"input": ""}], "time_limit": time_limit}
    response = client.post("/run", json=body)
    assert response.status_code == 200, response.text
    return response.json()["results"]


def test_health_ok():
    assert client.get("/health").json() == {"status": "ok"}


def test_correct_solution_runs_and_reads_stdin():
    results = run(
        "a, b = map(int, input().split())\nprint(a + b)",
        tests=[{"input": "2 3\n"}, {"input": "10 5\n"}],
    )
    assert [r["status"] for r in results] == ["ok", "ok"]
    assert [r["stdout"].strip() for r in results] == ["5", "15"]
    assert results[0]["exit_code"] == 0


def test_runtime_error_is_reported_with_traceback():
    (result,) = run("print(1 / 0)")
    assert result["status"] == "error"
    assert result["exit_code"] != 0
    assert "ZeroDivisionError" in result["stderr"]


def test_infinite_loop_is_stopped():
    # Чистий цикл з'їдає процесорний час: його обриває RLIMIT_CPU або стінний таймаут.
    (result,) = run("while True:\n    pass", time_limit=1)
    assert result["status"] in ("error", "timeout")


def test_sleep_longer_than_limit_is_a_timeout():
    # sleep не витрачає CPU, тож спрацьовує саме стінний таймаут.
    (result,) = run("import time\ntime.sleep(10)", time_limit=1)
    assert result["status"] == "timeout"


def test_fork_bomb_is_contained_and_server_survives():
    (result,) = run("import os\nwhile True:\n    os.fork()", time_limit=2)
    assert result["status"] in ("error", "timeout")
    # Наступний звичайний запуск усе ще обслуговується: процеси бомби прибрані.
    (ok,) = run("print('alive')")
    assert ok["status"] == "ok"
    assert ok["stdout"].strip() == "alive"


def test_memory_hog_hits_the_limit():
    (result,) = run("x = bytearray(1024 * 1024 * 1024)\nprint(len(x))")
    assert result["status"] == "error"
    assert "alive" not in result["stdout"]


def test_large_file_write_is_blocked():
    code = "with open('big', 'wb') as f:\n    f.write(b'x' * (50 * 1024 * 1024))\nprint('written')"
    (result,) = run(code)
    assert result["status"] == "error"
    assert "written" not in result["stdout"]


def test_runs_are_isolated_from_each_other():
    run("open('marker', 'w').write('x')")
    (result,) = run("import os\nprint(os.path.exists('marker'))")
    assert result["stdout"].strip() == "False"


def test_output_is_truncated():
    (result,) = run("print('A' * 200000)")
    assert len(result["stdout"]) < 200000
    assert "обрізано" in result["stdout"]


def test_unsupported_language_is_rejected():
    body = {"language": "javascript", "code": "console.log(1)", "tests": [{"input": ""}]}
    assert client.post("/run", json=body).status_code == 400


def test_invalid_requests_are_rejected():
    assert client.post("/run", json={"language": "python", "code": "x", "tests": []}).status_code == 422
    too_many = {"language": "python", "code": "x", "tests": [{"input": ""}] * 51}
    assert client.post("/run", json=too_many).status_code == 422
    too_long = {"language": "python", "code": "x" * 20_001, "tests": [{"input": ""}]}
    assert client.post("/run", json=too_long).status_code == 422

"""Виконує один фрагмент коду в ізоляції: обмеження ресурсів + скидання прав до користувача sandbox.

Linux-only: використовує resource.setrlimit, os.fork (через subprocess) і setuid. Контейнер runner
додає решту (окрема мережа без інтернету, read-only ФС, cap_drop, pids_limit) — дивись compose.yaml.
"""
import os
import pwd
import resource
import shutil
import subprocess
import sys
import tempfile
import uuid

# Куди писати тимчасові теки запусків. У контейнері це tmpfs /sandbox; інакше системний temp.
BASE_DIR = os.environ.get('SANDBOX_DIR', '/sandbox')
if not os.path.isdir(BASE_DIR):
    BASE_DIR = tempfile.gettempdir()

# Скільки байтів виводу повертаємо; решту відкидаємо.
MAX_OUTPUT_BYTES = 64 * 1024
# Межі одного запуску.
MEMORY_LIMIT = 256 * 1024 * 1024
FILE_SIZE_LIMIT = 10 * 1024 * 1024
# Кількість процесів користувача sandbox: ловить fork-бомбу ще до ліміту pids контейнера.
PROCESS_LIMIT = 64
# Понад ліміт процесорного часу — скільки чекати на зависання за стінним годинником.
WALL_CLOCK_BUFFER = 1


def _sandbox_user():
    """uid і gid користувача sandbox, або None поза контейнером (тоді права не скидаємо)."""
    if os.geteuid() != 0:
        return None
    try:
        record = pwd.getpwnam('sandbox')
    except KeyError:
        return None
    return record.pw_uid, record.pw_gid


def _child_setup(cpu_seconds, run_dir, user):
    """Виконується у дочірньому процесі між fork і exec: ставить межі й скидає права."""
    def apply():
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
        resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_SIZE_LIMIT, FILE_SIZE_LIMIT))
        resource.setrlimit(resource.RLIMIT_NPROC, (PROCESS_LIMIT, PROCESS_LIMIT))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.chdir(run_dir)
        if user is not None:
            # Групу — до uid: після скидання uid змінити групу вже не можна.
            os.setgid(user[1])
            os.setuid(user[0])
    return apply


def _read_capped(path):
    with open(path, 'rb') as handle:
        data = handle.read(MAX_OUTPUT_BYTES + 1)
    text = data[:MAX_OUTPUT_BYTES].decode('utf-8', 'replace')
    if len(data) > MAX_OUTPUT_BYTES:
        text += '\n…(вивід обрізано)'
    return text


def run_code(code, stdin_text, time_limit):
    """Запускає code з stdin_text на вході. Повертає status, exit_code, stdout, stderr.

    status: 'ok' — завершився сам (код у exit_code), 'timeout' — перевищив час, 'error' — впав.
    """
    user = _sandbox_user()
    run_dir = os.path.join(BASE_DIR, uuid.uuid4().hex)
    os.mkdir(run_dir, mode=0o700)
    try:
        solution = os.path.join(run_dir, 'solution.py')
        with open(solution, 'w', encoding='utf-8') as handle:
            handle.write(code)
        stdin_path = os.path.join(run_dir, 'stdin')
        stdout_path = os.path.join(run_dir, 'stdout')
        stderr_path = os.path.join(run_dir, 'stderr')
        with open(stdin_path, 'wb') as stdin_file:
            stdin_file.write(stdin_text.encode('utf-8'))
        # Вивід пишемо у файли на tmpfs: RLIMIT_FSIZE і розмір tmpfs обмежують його, а не пам'ять сервера.
        open(stdout_path, 'wb').close()
        open(stderr_path, 'wb').close()
        if user is not None:
            # Процес під sandbox має читати код і вхід та писати свій вивід у цій теці.
            for name in (run_dir, solution, stdin_path, stdout_path, stderr_path):
                os.chown(name, user[0], user[1])

        env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': run_dir, 'LANG': 'C.UTF-8'}
        status = 'ok'
        exit_code = None
        with open(stdin_path, 'rb') as stdin_handle, \
                open(stdout_path, 'wb') as out, open(stderr_path, 'wb') as err:
            process = subprocess.Popen(
                [sys.executable, '-I', '-B', 'solution.py'],
                cwd=run_dir, stdin=stdin_handle, stdout=out, stderr=err, env=env,
                # The only way to set rlimits and drop privileges in the child before exec; the
                # server process forks here without holding any lock, so the thread warning is moot.
                preexec_fn=_child_setup(time_limit, run_dir, user),  # noqa: PLW1509
                start_new_session=True,
            )
            try:
                exit_code = process.wait(timeout=time_limit + WALL_CLOCK_BUFFER)
            except subprocess.TimeoutExpired:
                status = 'timeout'
            finally:
                if process.poll() is None:
                    # start_new_session зробив процес лідером групи: вбиваємо всіх його нащадків (fork-бомба).
                    try:
                        os.killpg(process.pid, 9)
                    except ProcessLookupError:
                        pass
                    process.wait()
        if status != 'timeout' and exit_code != 0:
            status = 'error'
        return {
            'status': status,
            'exit_code': exit_code,
            'stdout': _read_capped(stdout_path),
            'stderr': _read_capped(stderr_path),
        }
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

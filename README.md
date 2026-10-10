# KellyLab

Backend для підготовки до технічних співбесід. Робочий сценарій: реєстрація → профіль → сесія → імпорт питань → відповіді → власні правки → TXT-експорт.

## Що працює

- Реєстрація через email, JWT access/refresh, редагування посади та рівня.
- Сесії та картки питань, доступні лише власнику.
- Імпорт тексту або UTF-8 TXT: одне питання на рядок, максимум 50 питань, 2000 символів на питання та 100 KB на файл. Повторний імпорт у заповнену сесію відхиляється.
- Генерація через Celery + Redis; для локального запуску можна виконувати задачі синхронно.
- Генерація відповідей Gemini через `google-genai`.
- Окреме зберігання AI-відповіді та власної відповіді; повторна генерація зберігає власні правки.
- TXT-експорт, Swagger, інтеграційні тести й CI з PostgreSQL.

Це API-MVP. React, PDF/DOCX parser/exporter колеги, AI-практика, перевірка коду, pgvector та Llama fallback ще не підключені. Наявні моделі `practice` і `exports` залишаються заготовками. TXT-експорт формується одразу без запису `Export`. LangChain/LangGraph поки не потрібні для одного виклику моделі.

## Локальний запуск у PowerShell

Спочатку в наявному `.env` задай `GEMINI_API_KEY` і `GEMINI_MODEL` (дивись нижче). З наявним `venv`:

```powershell
$env:USE_SQLITE = 'true'
$env:CELERY_TASK_ALWAYS_EAGER = 'true'
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py runserver
```

Існуючий `.env` не змінювався. Змінні поточного процесу мають пріоритет. На чистій машині потрібен Python 3.13: `python -m venv venv`, потім `.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt`. Скопіюй `.env.example` у `.env`, лише якщо власного `.env` ще немає. `requirements.txt` містить залежності застосунку, `requirements-dev.txt` додає інструменти розробки (ruff).

Відкрий http://127.0.0.1:8000/api/docs/. У Swagger після отримання JWT натисни **Authorize** і введи `Bearer <access>`.

В іншому PowerShell можна запустити весь сценарій:

```powershell
.\examples\smoke-test.ps1
```

Скрипт створює окремого користувача, імпортує `examples/questions.txt`, робить п'ять справжніх запитів до Gemini і записує `examples/smoke-export.txt`. Пароль і токени не виводяться. Без ключа або ID моделі генерація завершиться помилкою; підстановки відповідей немає.

## Docker: PostgreSQL + Redis + Django + Celery

```powershell
docker compose --env-file .env up --build
```

Міграції виконує окремий сервіс перед стартом web/worker. API: http://127.0.0.1:8000/api/docs/. PostgreSQL зберігається в named volume. `docker compose down` зупиняє сервіси, зберігаючи дані. Це конфігурація для локальної розробки, з Django development server.

## Справжні AI-відповіді

У `.env` або змінних середовища встанови:

```dotenv
GEMINI_API_KEY=your-key
GEMINI_MODEL=model-id-available-to-your-account
```

Перезапусти Django і worker; у Docker використай `docker compose --env-file .env up --build` із власними значеннями. Якщо секрет містить `$`, у dotenv візьми його в одинарні лапки, щоб Compose не сприйняв частину секрету як змінну. ID моделі задається явно, оскільки доступність залежить від акаунта. Питання, посада та рівень надсилаються в API Google; запити можуть бути платними. SDK використовується за [офіційною документацією Google](https://googleapis.github.io/python-genai/).

Для фонового режиму поза Docker потрібні Redis і `CELERY_TASK_ALWAYS_EAGER=false`; worker: `celery -A config worker --loglevel=info` у Linux/WSL. Для локального запуску на Windows простіше eager або Docker. Eager блокує HTTP-запит до завершення генерації, але теж звертається до справжнього Gemini.

## API

У захищених запитах передавай `Authorization: Bearer <access>`.

Access діє 15 хвилин, refresh — 7 днів. Кожен виклик `token/refresh/` повертає новий refresh, а старий одразу відкликається, тому зберігай новий. Якщо refresh не використовувати 7 днів, потрібен повторний вхід. Після `logout/` refresh більше не працює, але вже виданий access діє до кінця своїх 15 хвилин. Прострочені записи blacklist прибирає `python manage.py flushexpiredtokens`.

| Метод | Шлях | Призначення |
| --- | --- | --- |
| POST | `/api/auth/register/` | `email`, `password`; профіль необов'язковий |
| POST | `/api/auth/token/` | Вхід: `email`, `password` |
| POST | `/api/auth/token/refresh/` | Новий access і новий refresh через `refresh` |
| POST | `/api/auth/logout/` | Вихід: `refresh` потрапляє в blacklist |
| GET, PATCH | `/api/auth/me/` | `target_position`, `experience_level` |
| GET, POST | `/api/sessions/` | Список / створення з `name` |
| GET, PATCH, DELETE | `/api/sessions/{id}/` | Перегляд, назва, видалення |
| POST | `/api/sessions/{id}/import/` | JSON `{"text":"Питання 1\nПитання 2"}` або multipart `file` |
| POST | `/api/sessions/{id}/generate/` | Генерація: тіло порожнє або `{"mode":"missing"}` — лише питання без AI-відповіді; `{"mode":"all"}` — усі заново |
| GET | `/api/sessions/{id}/questions/` | Картки з пагінацією |
| GET, PATCH | `/api/questions/{id}/` | `text`, `user_answer` |
| GET | `/api/sessions/{id}/export/` | Завантаження UTF-8 TXT |

Списки мають поля `count`, `next`, `previous`, `results`; наступна сторінка — `?page=2`. Статус сесії читається через GET: `created → processing → ready / failed`. Відповідь 202 означає прийнятий запуск, а не гарантований успіх AI; перевіряй `status` і `error_message`. Під час обробки зміни карток, видалення, повторний запуск та експорт повертають 409. Невдала публікація в чергу повертає 503 і встановлює `failed`.

`user_answer=null` повертає показ AI-відповіді; `user_answer=""` навмисно залишає відповідь порожньою. Зміна тексту питання очищує його AI-відповідь і повертає сесію в `created`. За замовчуванням генерація відповідає лише на питання без AI-відповіді (нові, змінені або ті, що впали з помилкою), тож зміна одного питання коштує одного виклику AI; якщо таких питань немає, повертається 400. `mode=all` оновлює всі AI-відповіді сесії. Кожна відповідь зберігається одразу, тож після помилки готові відповіді лишаються. Кожен виклик AI повторюється до 3 разів із паузою.

## Перевірки

```powershell
.\venv\Scripts\python.exe manage.py test --settings=config.test_settings
.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.test_settings
```

Локальні тести використовують окрему SQLite-базу в пам'яті. Зовнішній SDK Gemini заміняється через `unittest.mock` лише всередині тестів; у застосунку генерація завжди звертається до Gemini. CI запускає тести на PostgreSQL. Перевіряються JWT, повний сценарій, ізоляція користувачів, валідація файлів, помилки AI/черги, відсутня конфігурація та збереження правок. Живі Gemini, Redis, Docker і конкурентні блокування PostgreSQL потребують окремого інтеграційного запуску.

Якщо worker впав, Celery доставить задачу повторно (`acks_late`). Сесія, яка 10 хвилин (`GENERATION_STALE_AFTER`) не зберегла жодної відповіді, вважається завислою: її можна запустити повторно або видалити без 409. Задача обмежена 30 хвилинами. Для production ще потрібні обмеження частоти AI-запитів, контроль бюджету й перевірка налаштувань розгортання.

Маршрут розбору на завтра: [docs/STUDY_GUIDE.md](docs/STUDY_GUIDE.md).

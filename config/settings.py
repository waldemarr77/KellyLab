import os
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()


def env_bool(name, default='false'):
    return os.getenv(name, default).lower() == 'true'


def env_list(name, default=''):
    return [item.strip() for item in os.getenv(name, default).split(',') if item.strip()]


BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = env_bool('DEBUG')

SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured('SECRET_KEY must be set when DEBUG is false.')
    SECRET_KEY = 'local-development-only-change-before-deployment'

ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', 'localhost,127.0.0.1')


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'corsheaders',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'drf_yasg',

    'exports.apps.ExportsConfig',
    'interviews.apps.InterviewsConfig',
    'practice.apps.PracticeConfig',
    'questions.apps.QuestionsConfig',
    'users.apps.UsersConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# Vite dev server by default; set the real frontend origins in production.
CORS_ALLOWED_ORIGINS = env_list('CORS_ALLOWED_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173')

REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': ('rest_framework.permissions.IsAuthenticated',),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    # Scoped throttles are enabled per view with throttle_scope.
    'DEFAULT_THROTTLE_RATES': {
        'auth': os.getenv('AUTH_THROTTLE_RATE', '10/minute'),
        'generate': os.getenv('GENERATE_THROTTLE_RATE', '2/minute'),
        'token': os.getenv('TOKEN_THROTTLE_RATE', '30/minute'),
        'export': os.getenv('EXPORT_THROTTLE_RATE', '10/minute'),
        # Creating sessions and importing questions; both only grow the database.
        'session_write': os.getenv('SESSION_WRITE_THROTTLE_RATE', '60/hour'),
        'verification_email': os.getenv('VERIFICATION_EMAIL_THROTTLE_RATE', '3/hour'),
    },
    # Number of reverse proxies in front of Django. With 0 the client is REMOTE_ADDR and
    # X-Forwarded-For is ignored; otherwise only the addresses appended by our proxies count.
    'NUM_PROXIES': int(os.getenv('NUM_PROXIES', '0')),
}

# Each refresh returns a new refresh token and blacklists the old one, so a stolen
# token stops working once either party uses it. Logout blacklists the current one.
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST', '127.0.0.1'),
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}

# SQLite is an explicit option for local development and isolated tests.
if env_bool('USE_SQLITE'):
    DATABASES = {'default': {
        'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3',
    }}

SWAGGER_SETTINGS = {
    'SECURITY_DEFINITIONS': {'Bearer': {'type': 'apiKey', 'name': 'Authorization', 'in': 'header'}},
    'USE_SESSION_AUTH': False,
}

# Throttle counters must be shared by all web processes and survive restarts.
# Without CACHE_URL Django falls back to a per-process in-memory cache (local development only).
CACHE_URL = os.getenv('CACHE_URL', '')
if CACHE_URL:
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.redis.RedisCache', 'LOCATION': CACHE_URL}}

CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://127.0.0.1:6379/0')
CELERY_TASK_ALWAYS_EAGER = env_bool('CELERY_TASK_ALWAYS_EAGER')
CELERY_TASK_IGNORE_RESULT = True
CELERY_BROKER_CONNECTION_TIMEOUT = 3
CELERY_TASK_PUBLISH_RETRY = False
# Re-deliver a task if the worker dies mid-run instead of losing it.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
# Gemini's rate limit is per key and the pause between requests is kept per task,
# so generation runs in its own queue served by a single worker process.
CELERY_TASK_ROUTES = {'questions.tasks.generate_session_answers': {'queue': 'ai'}}
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_MODEL = os.getenv('GEMINI_MODEL', '')
AI_MAX_ATTEMPTS = 3
AI_RETRY_DELAY_SECONDS = 2
# Free tier allows 15 requests per minute, so keep at least 60 / 15 = 4 s between requests.
AI_MIN_INTERVAL_SECONDS = float(os.getenv('AI_MIN_INTERVAL_SECONDS', '4'))
# A 429 means the per-minute window is exhausted; short retries would hit it again.
AI_RATE_LIMIT_RETRY_DELAY_SECONDS = 30
# Output tokens per answer. Thinking models count their reasoning here too,
# so a small limit cuts answers off or leaves them empty.
AI_MAX_OUTPUT_TOKENS = int(os.getenv('AI_MAX_OUTPUT_TOKENS', '8192'))
# Empty: the model's default thinking. 0 turns it off on models that allow it (e.g. 2.5 Flash).
AI_THINKING_BUDGET = int(os.getenv('AI_THINKING_BUDGET')) if os.getenv('AI_THINKING_BUDGET') else None
# Shared free-tier key: cap how many questions one user can send to Gemini per day.
AI_DAILY_QUESTION_LIMIT = int(os.getenv('AI_DAILY_QUESTION_LIMIT', '100'))
# The whole key gets 500 requests a day; retries are not charged here, so keep a margin.
AI_GLOBAL_DAILY_QUESTION_LIMIT = int(os.getenv('AI_GLOBAL_DAILY_QUESTION_LIMIT', '400'))
# Gemini resets daily quotas at midnight Pacific time, not UTC.
GEMINI_QUOTA_TIMEZONE = ZoneInfo('America/Los_Angeles')
# A processing session without saved progress for this long is treated as stuck
# and can be regenerated or deleted. Must exceed the worst case for one question.
GENERATION_STALE_AFTER = timedelta(minutes=10)
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
# DATA_UPLOAD_MAX_MEMORY_SIZE does not count files, so the handler below cuts off a file at this size.
FILE_UPLOAD_MAX_SIZE = 100_000
FILE_UPLOAD_HANDLERS = [
    'config.uploads.FileSizeLimitUploadHandler',
    'django.core.files.uploadhandler.MemoryFileUploadHandler',
    'django.core.files.uploadhandler.TemporaryFileUploadHandler',
]
# Every session holds up to 50 questions, so this bounds how much one account can store.
MAX_SESSIONS_PER_USER = int(os.getenv('MAX_SESSIONS_PER_USER', '50'))
MEDIA_ROOT = BASE_DIR / 'media'
MEDIA_URL = 'media/'
EXPORTER_URL = os.getenv('EXPORTER_URL', 'http://127.0.0.1:8002')
EXPORTER_TIMEOUT_SECONDS = 60
# Longer than the export task's time limit (6 min): an older pending/processing export is dead.
EXPORT_STALE_AFTER = timedelta(minutes=10)
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

AUTH_USER_MODEL = 'users.CustomUser'

AUTHENTICATION_BACKENDS = ['users.backends.LoginLimitBackend']
# Failed logins (API and admin) are counted per email and per IP; at the limit the next
# attempts are refused without checking the password until the window ends.
LOGIN_FAILURE_LIMIT_PER_EMAIL = int(os.getenv('LOGIN_FAILURE_LIMIT_PER_EMAIL', '5'))
LOGIN_FAILURE_LIMIT_PER_IP = int(os.getenv('LOGIN_FAILURE_LIMIT_PER_IP', '20'))
LOGIN_FAILURE_WINDOW = timedelta(minutes=15)


LANGUAGE_CODE = 'uk'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'

# Without EMAIL_HOST letters are printed to the console (local development).
# Lowercase name: Django forbids the old EMAIL_* settings next to MAILERS.
email_host = os.getenv('EMAIL_HOST', '')
if email_host:
    MAILERS = {'default': {
        'BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
        'OPTIONS': {
            'host': email_host,
            'port': int(os.getenv('EMAIL_PORT', '587')),
            'username': os.getenv('EMAIL_HOST_USER', ''),
            'password': os.getenv('EMAIL_HOST_PASSWORD', ''),
            'use_tls': env_bool('EMAIL_USE_TLS', 'true'),
            'timeout': 10,
        },
    }}
else:
    MAILERS = {'default': {'BACKEND': 'django.core.mail.backends.console.EmailBackend'}}
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'KellyLab <noreply@localhost>')
# Frontend page that reads uid and token from the link and POSTs them to /api/auth/verify-email/.
EMAIL_VERIFICATION_URL = os.getenv(
    'EMAIL_VERIFICATION_URL', 'http://localhost:5173/verify-email?uid={uid}&token={token}',
)
# Frontend page that asks for a new password and POSTs it with uid and token to /api/auth/password/reset/confirm/.
PASSWORD_RESET_URL = os.getenv('PASSWORD_RESET_URL', 'http://localhost:5173/reset-password?uid={uid}&token={token}')

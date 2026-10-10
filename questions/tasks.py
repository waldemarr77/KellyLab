import logging
import time

import httpx
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from google.genai import errors as genai_errors

from interviews.models import Session, SessionStatus

from .ai import answer_generator
from .models import Question

logger = logging.getLogger(__name__)

TRANSIENT_STATUS_CODES = {408, 429}

def is_transient(exc):
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, genai_errors.APIError):
        return exc.code in TRANSIENT_STATUS_CODES or exc.code >= 500
    return False

def generate_with_retries(generate, question):
    for attempt in range(1, settings.AI_MAX_ATTEMPTS + 1):
        try:
            return generate(question)
        except Exception as exc:
            if not is_transient(exc) or attempt == settings.AI_MAX_ATTEMPTS:
                raise
            logger.info('Transient AI error, retrying (attempt %s)', attempt)
            time.sleep(settings.AI_RETRY_DELAY_SECONDS * 2 ** (attempt - 1))


# Stay well below the Redis visibility timeout (1 h): with acks_late a longer
# task would be delivered to a second worker.
@shared_task(soft_time_limit=30 * 60, time_limit=31 * 60)
def generate_session_answers(session_id, run_id, mode='missing'):
    active_run = Session.objects.filter(pk=session_id, status=SessionStatus.PROCESSING, celery_task_id=run_id)
    session = active_run.select_related('user').first()
    if session is None:
        return
    questions = session.questions.order_by('order', 'id')
    if mode == 'missing':
        # Edited questions have their AI answer cleared, so only they are sent to the AI.
        questions = questions.filter(ai_answer__isnull=True)
    try:
        with answer_generator(session.user.target_position, session.user.experience_level) as generate:
            for question in questions:
                # Keep network calls outside a database transaction.
                answer = generate_with_retries(generate, question.text)
                with transaction.atomic():
                    # Locks the session row and refreshes updated_at, which serves as
                    # the heartbeat for stale-run detection (Session.is_busy).
                    if not active_run.update(updated_at=timezone.now()):
                        return  # A newer run took over or the session was deleted.
                    Question.objects.filter(pk=question.pk).update(ai_answer=answer)
        active_run.update(status=SessionStatus.READY, error_message='', updated_at=timezone.now())
    except Exception:  # noqa: BLE001 -- task boundary must persist failed state for any provider failure
        # Do not persist provider errors: they can contain credentials or private input.
        # Answers saved before the failure are kept.
        logger.warning('Answer generation failed for session %s', session_id)
        active_run.update(
            status=SessionStatus.FAILED,
            error_message='Генерація не вдалася. Перевір налаштування AI та повтори.',
            updated_at=timezone.now(),
        )

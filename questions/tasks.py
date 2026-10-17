import logging
import time

import httpx
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from google.genai import errors as genai_errors

from interviews.models import Session, SessionStatus
from interviews.quota import refund_daily_quota

from .ai import BadAIResponse, IncompleteAIResponse, answer_generator, render_answer
from .models import Question

logger = logging.getLogger(__name__)

TRANSIENT_STATUS_CODES = {408, 429}

def is_transient(exc):
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, genai_errors.APIError):
        return exc.code in TRANSIENT_STATUS_CODES or exc.code >= 500
    return False

def retry_delay(exc, attempt):
    if isinstance(exc, genai_errors.APIError) and exc.code == 429:
        return settings.AI_RATE_LIMIT_RETRY_DELAY_SECONDS
    return settings.AI_RETRY_DELAY_SECONDS * 2 ** (attempt - 1)

def describe_error(exc):
    """Name the error and its HTTP status, never its message: it can hold the key or user text."""
    if isinstance(exc, IncompleteAIResponse):
        return f'{type(exc).__name__} ({exc.reason})'
    status = exc.code if isinstance(exc, genai_errors.APIError) else None
    return f'{type(exc).__name__} (status {status})' if status else type(exc).__name__

def throttled(generate):
    """Wrap generate so consecutive calls are at least AI_MIN_INTERVAL_SECONDS apart."""
    last_call = None

    def call(question):
        nonlocal last_call
        if last_call is not None:
            remaining = settings.AI_MIN_INTERVAL_SECONDS - (time.monotonic() - last_call)
            if remaining > 0:
                time.sleep(remaining)
        last_call = time.monotonic()
        return generate(question)

    return call

def generate_with_retries(generate, question):
    for attempt in range(1, settings.AI_MAX_ATTEMPTS + 1):
        try:
            return generate(question)
        except Exception as exc:
            if not is_transient(exc) or attempt == settings.AI_MAX_ATTEMPTS:
                raise
            logger.info(
                'Transient AI error %s, retrying (attempt %s of %s)',
                describe_error(exc), attempt, settings.AI_MAX_ATTEMPTS,
            )
            time.sleep(retry_delay(exc, attempt))


# Stay well below the Redis visibility timeout (1 h): with acks_late a longer
# task would be delivered to a second worker.
@shared_task(soft_time_limit=30 * 60, time_limit=31 * 60)
def generate_session_answers(session_id, run_id, mode='missing', charge=None):
    active_run = Session.objects.filter(pk=session_id, status=SessionStatus.PROCESSING, celery_task_id=run_id)
    session = active_run.select_related('user').first()
    if session is None:
        # No refund here: a redelivered copy of a finished run also lands here, and its questions were sent.
        return
    # The run may have waited in the queue; mark it alive before the first, possibly slow, answer.
    if not active_run.update(updated_at=timezone.now()):
        return
    questions = session.questions.order_by('order', 'id')
    if mode == 'missing':
        # Edited questions have their AI answer cleared, so only they are sent to the AI.
        questions = questions.filter(ai_answer__isnull=True)
    questions = list(questions)
    sent = failed = 0
    failed_question_id = None  # Stays None if the failure happens before the first question.
    try:
        with answer_generator(session.user.target_position, session.user.experience_level) as generate:
            # Retries go through the same wrapper, so they also respect the interval.
            generate = throttled(generate)
            for question in questions:
                failed_question_id = question.pk
                sent += 1
                # Keep network calls outside a database transaction.
                try:
                    parts = generate_with_retries(generate, question.text)
                    fields = {'ai_answer': render_answer(parts), 'ai_parts': parts, 'ai_error': ''}
                except BadAIResponse as exc:
                    # The answer is bad for this question only (safety block, cut off): note it and go on.
                    # A previous answer, if any, is kept.
                    logger.warning(
                        'No usable answer for session %s, question %s: %s', session_id, question.pk, describe_error(exc),
                    )
                    fields = {'ai_error': exc.user_message}
                    failed += 1
                with transaction.atomic():
                    # Locks the session row and refreshes updated_at, which serves as
                    # the heartbeat for stale-run detection (Session.is_busy).
                    if not active_run.update(updated_at=timezone.now()):
                        return  # A newer run took over or the session was deleted.
                    Question.objects.filter(pk=question.pk).update(**fields)
        active_run.update(
            status=SessionStatus.READY,
            error_message=(
                f'Не вдалося згенерувати відповіді на {failed} з {len(questions)} питань. '
                'Причину видно в картках; зміни ці питання й запусти генерацію ще раз.'
            ) if failed else '',
            updated_at=timezone.now(),
        )
    except Exception as exc:  # noqa: BLE001 -- task boundary must persist failed state for any provider failure
        # Errors that would repeat for every question (key, model, quota, network) stop the run.
        # Do not persist or log provider error messages: they can contain credentials or private input.
        # Answers saved before the failure are kept.
        logger.warning(
            'Answer generation failed for session %s, question %s: %s',
            session_id, failed_question_id, describe_error(exc),
        )
        active_run.update(
            status=SessionStatus.FAILED,
            error_message='Генерація не вдалася. Перевір налаштування AI та повтори.',
            updated_at=timezone.now(),
        )
    finally:
        # Questions the run never got to were charged at launch; give them back.
        if charge:
            refund_daily_quota(charge, charge['questions'] - sent)

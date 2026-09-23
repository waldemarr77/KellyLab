import logging

from celery import shared_task
from django.db import transaction

from sessions.models import Session

from .ai import generate_answer
from .models import Question

logger = logging.getLogger(__name__)


@shared_task
def generate_session_answers(session_id, run_id):
    session = Session.objects.select_related('user').filter(
        pk=session_id, status='processing', celery_task_id=run_id,
    ).first()
    if session is None:
        return
    try:
        # Keep network calls outside a database transaction. Commit all answers together.
        answers = [
            (q.id, generate_answer(q.text, session.user.target_position, session.user.experience_level))
            for q in session.questions.order_by('order', 'id')
        ]
        with transaction.atomic():
            current = Session.objects.select_for_update().filter(
                pk=session_id, status='processing', celery_task_id=run_id,
            ).first()
            if current is None:
                return
            for question_id, answer in answers:
                Question.objects.filter(pk=question_id, session=current).update(ai_answer=answer)
            current.status = 'ready'
            current.error_message = ''
            current.save(update_fields=['status', 'error_message', 'updated_at'])
    except Exception:  # noqa: BLE001 -- task boundary must persist failed state for any provider failure
        # Do not persist provider errors: they can contain credentials or private input.
        logger.warning('Answer generation failed for session %s', session_id)
        Session.objects.filter(pk=session_id, status='processing', celery_task_id=run_id).update(
            status='failed', error_message='Генерація не вдалася. Перевір налаштування AI та повтори.',
        )

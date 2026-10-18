from django.db import transaction
from kombu.exceptions import OperationalError
from rest_framework.exceptions import PermissionDenied, ValidationError

from config.exceptions import Conflict
from interviews.quota import charge_daily_quota, refund_daily_quota

from .models import Difficulty, PracticeStatus, QuestionPractice
from .tasks import generate_practice, review_solution

QUEUE_ERROR = 'Черга недоступна. Перевір Redis та Celery.'


class QueueUnavailable(Exception):
    pass


def require_verified(user):
    # Registration is open, so only confirmed accounts may spend the shared Gemini quota.
    if not user.email_verified:
        raise PermissionDenied('Підтверди пошту, щоб користуватися AI-практикою.')


def publish(task, practice, charge, *args):
    """Send the task after commit; on a queue failure give the quota back and mark the practice."""
    try:
        task.delay(practice.pk, *args, charge=charge)
    except (OperationalError, ConnectionError) as exc:
        # Nothing reached Gemini, so the request must not cost any quota.
        refund_daily_quota(charge, charge['questions'])
        QuestionPractice.objects.filter(pk=practice.pk).update(status=PracticeStatus.ERROR, error_message=QUEUE_ERROR)
        raise QueueUnavailable from exc


def start_generation(user, question, difficulty=None):
    """Create a practice for the question and queue its generation; one Gemini request is charged."""
    require_verified(user)
    with transaction.atomic():
        # Raises before anything is created, so a rejected request leaves no empty practice behind.
        charge = charge_daily_quota(user, 1)
        # Without a requested difficulty the model picks one; easy is only a placeholder until then.
        practice = QuestionPractice.objects.create(
            question=question, status=PracticeStatus.GENERATING, difficulty=difficulty or Difficulty.EASY,
        )
    # Commit before publishing: even a fast worker must see the new practice.
    publish(generate_practice, practice, charge, difficulty)
    return QuestionPractice.objects.filter(pk=practice.pk).first()


def start_review(user, practice, code):
    """Store the solution and queue its review; one Gemini request is charged."""
    require_verified(user)
    with transaction.atomic():
        practice = QuestionPractice.objects.select_for_update().get(pk=practice.pk)
        if practice.is_busy:
            raise Conflict('Дочекайся завершення генерації або перевірки.')
        if not practice.description:
            raise ValidationError('Завдання ще не згенероване. Створи нове.')
        charge = charge_daily_quota(user, 1)
        practice.user_solution = code
        practice.status = PracticeStatus.CHECKING
        practice.feedback = ''
        practice.error_message = ''
        practice.save(update_fields=['user_solution', 'status', 'feedback', 'error_message', 'updated_at'])
    publish(review_solution, practice, charge)
    return QuestionPractice.objects.filter(pk=practice.pk).first()

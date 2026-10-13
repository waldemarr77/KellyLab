from django.conf import settings
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone
from rest_framework.exceptions import Throttled

from .models import AIUsage, GlobalAIUsage


def locked_usage(queryset, **lookup):
    usage, _ = queryset.get_or_create(**lookup)
    return queryset.select_for_update().get(pk=usage.pk)


def charge_daily_quota(user, question_count):
    """Count questions sent to Gemini today; reject the run if it exceeds the user or service limit.

    Returns the charge, which refund_daily_quota needs to return unsent questions to the same days.
    """
    # Every run locks the service row first, so parallel runs cannot deadlock or both pass the check.
    service = locked_usage(
        GlobalAIUsage.objects, date=timezone.localdate(timezone=settings.GEMINI_QUOTA_TIMEZONE),
    )
    usage = locked_usage(AIUsage.objects, user=user, date=timezone.localdate())
    limit = settings.AI_DAILY_QUESTION_LIMIT
    if usage.questions + question_count > limit:
        raise Throttled(detail=(
            f'Денний ліміт генерації вичерпано: використано {usage.questions} з {limit} питань, '
            f'а запуск потребує {question_count}. Спробуй завтра.'
        ))
    if service.questions + question_count > settings.AI_GLOBAL_DAILY_QUESTION_LIMIT:
        raise Throttled(detail='Сервіс сьогодні вичерпав ліміт генерації. Спробуй завтра.')
    for row in (usage, service):
        row.questions = F('questions') + question_count
        row.save(update_fields=['questions'])
    # Plain values: the charge travels to the Celery task as JSON.
    return {
        'user_id': user.pk, 'user_date': usage.date.isoformat(), 'service_date': service.date.isoformat(),
        'questions': question_count,
    }


def refund_daily_quota(charge, question_count):
    """Give back questions that were charged but never sent to Gemini."""
    if question_count <= 0:
        return
    # Greatest: an admin may have reset the counter after the charge.
    refunded = Greatest(F('questions') - question_count, 0)
    GlobalAIUsage.objects.filter(date=charge['service_date']).update(questions=refunded)
    AIUsage.objects.filter(user_id=charge['user_id'], date=charge['user_date']).update(questions=refunded)

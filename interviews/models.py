from django.conf import settings
from django.db import models
from django.utils import timezone

from users.models import CustomUser


class SessionStatus(models.TextChoices):
    CREATED = 'created', 'Сесія створена'
    PROCESSING = 'processing', 'Обробка'
    READY = 'ready', 'Готово'
    FAILED = 'failed', 'Помилка'


class Session(models.Model):
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        verbose_name='Користувач',
        related_name='sessions'
    )

    name = models.CharField(
        max_length=50, 
        verbose_name='Назва сесії')
    status = models.CharField(
        max_length=20,
        default=SessionStatus.CREATED,
        choices=SessionStatus.choices,
        verbose_name='Статус'
    )
    celery_task_id = models.CharField(
        max_length=255, 
        blank=True, 
        null=True, 
        verbose_name='ID задачі')
    error_message = models.TextField(blank=True, verbose_name='Пояснення помилки')
    created_at = models.DateTimeField(
        auto_now_add=True, 
        verbose_name='Створено')
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Оновлено'
    )

    class Meta:
        ordering = ['-created_at',]
        verbose_name = 'Сесія'
        verbose_name_plural = 'Сесії'

    def __str__(self):
        return f'{self.user} - {self.name}'

    @property
    def is_busy(self):
        """A run is active while it keeps saving answers; a silent one is considered dead."""
        return (
            self.status == SessionStatus.PROCESSING
            and self.updated_at > timezone.now() - settings.GENERATION_STALE_AFTER
        )


class AIUsage(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name='Користувач',
        related_name='ai_usage',
    )

    date = models.DateField(verbose_name='Дата')
    questions = models.PositiveIntegerField(default=0, verbose_name='Питання')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'date'], name='unique_ai_usage_per_day')]
        ordering = ['-date']
        verbose_name = 'Лічильник'
        verbose_name_plural = 'Лічильники'

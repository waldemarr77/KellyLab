from django.db import models

from sessions.models import Session


class FormatExport(models.TextChoices):
    PDF = 'pdf', 'PDF'
    DOCX = 'docx', 'DOCX'
    TXT = 'txt', 'TXT'


class StatusExport(models.TextChoices):
    PENDING = 'pending', 'В очікуванні експорту'
    PROCESSING = 'processing', 'В процесі'
    READY = 'ready', 'Готово'
    FAILED = 'failed', 'Невдало'


class Export(models.Model):
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name='exports',
        verbose_name='Сесія',
    )
    format = models.CharField(
        max_length=10,
        choices=FormatExport.choices,
        verbose_name='Формат',
    )
    status = models.CharField(
        max_length=25,
        choices=StatusExport.choices,
        default=StatusExport.PENDING,
        verbose_name='Статус',
    )
    file = models.FileField(
        upload_to='exports/%Y/%m/%d/',
        blank=True,
        verbose_name='Файл',
    )
    celery_task_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='ID фонової задачі',
    )
    content_hash = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name='Відбиток вмісту',
    )
    error_message = models.TextField(
        blank=True,
        verbose_name='Пояснення помилки',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Створено експорт',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Оновлено',
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Генерація завершилась',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Експорт'
        verbose_name_plural = 'Експорти'

    def __str__(self):
        return f'{self.session_id} - {self.get_format_display()}'
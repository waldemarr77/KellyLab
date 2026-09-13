from django.db import models
from users.models import CustomUser

class Session(models.Model):
    STATUS_CHOICES = [
        ('created', 'Сесія створена'),
        ('processing', 'Обробка'),
        ('ready', 'Готово'),
        ('failed', 'Помилка'),
    ]
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
        default='created',
        choices=STATUS_CHOICES,
        verbose_name='Статус'
    )
    celery_task_id = models.CharField(
        max_length=255, 
        blank=True, 
        null=True, 
        verbose_name='ID задачі')
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
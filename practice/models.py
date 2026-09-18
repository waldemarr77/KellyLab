from django.conf import settings
from django.db import models

from questions.models import Question


class PracticeStatus(models.TextChoices):
    NOT_STARTED = 'not_started', 'Ще не виконане'
    FAILED = 'failed', 'Провалено'
    PARTIAL = 'partial', 'Частково правильно виконано'
    SUCCESS = 'success', 'Успішно'


class QuestionPractice(models.Model):
    """Завдання від AI до питання; користувач пише код із нуля."""

    DIFFICULTY_CHOICES = [
        ('easy', 'Легка'),
        ('medium', 'Середня'),
        ('hard', 'Складна'),
    ]

    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name='practices',
        verbose_name='Питання',
    )
    title = models.CharField(
        max_length=255, 
        verbose_name='Назва завдання'
    )
    description = models.TextField(verbose_name='Умова завдання')
    difficulty = models.CharField(
        max_length=10,
        choices=DIFFICULTY_CHOICES,
        default='easy',
        verbose_name='Складність',
    )
    status = models.CharField(
        max_length=30,
        choices=PracticeStatus.choices,
        default=PracticeStatus.NOT_STARTED,
        verbose_name='Статус',
    )
    user_solution = models.TextField(
        blank=True, 
        verbose_name='Код користувача'
    )
    feedback = models.TextField(
        blank=True, 
        verbose_name='Пояснення оцінки'
    )
    created_at = models.DateTimeField(
        auto_now_add=True, 
        verbose_name='Створено'
    )
    updated_at = models.DateTimeField(
        auto_now=True, 
        verbose_name='Оновлено'
        )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Практика до питання'
        verbose_name_plural = 'Практики до питань'

    def __str__(self):
        return self.title[:30]


class CodingTask(models.Model):
    """Окрема задача з шаблоном коду, без прив'язки до питання."""

    DIFFICULTY_CHOICES = [
        ('easy', 'Easy'),
        ('medium', 'Medium'),
        ('hard', 'Hard'),
    ]

    title = models.CharField(
        max_length=255, 
        verbose_name='Назва задачі'
    )
    description = models.TextField(verbose_name='Умова задачі')
    difficulty = models.CharField(
        max_length=10,
        choices=DIFFICULTY_CHOICES,
        verbose_name='Складність',
    )
    language = models.CharField(
        max_length=50, 
        verbose_name='Мова програмування'
    )
    skeleton = models.TextField(verbose_name='Шаблон коду')
    test_cases = models.JSONField(
        default=list,
        blank=True,
        verbose_name='Перевірочні приклади',
        help_text='Список об’єктів із input та expected_output; формат визначає перевіряльник.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True, 
        verbose_name='Створено'
    )
    updated_at = models.DateTimeField(
        auto_now=True, 
        verbose_name='Оновлено'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Задача з програмування'
        verbose_name_plural = 'Задачі з програмування'

    def __str__(self):
        return self.title[:30]


class CodingAttempt(models.Model):
    """Окрема спроба користувача розв'язати задачу."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='coding_attempts',
        verbose_name='Користувач',
    )
    task = models.ForeignKey(
        CodingTask,
        on_delete=models.CASCADE,
        related_name='attempts',
        verbose_name='Задача',
    )
    user_solution = models.TextField(
        blank=True, 
        verbose_name='Код користувача'
    )
    status = models.CharField(
        max_length=30,
        choices=PracticeStatus.choices,
        default=PracticeStatus.NOT_STARTED,
        verbose_name='Статус',
    )
    feedback = models.TextField(
        blank=True, 
        verbose_name='Пояснення оцінки'
    )
    created_at = models.DateTimeField(
        auto_now_add=True, 
        verbose_name='Створено'
    )
    updated_at = models.DateTimeField(
        auto_now=True, 
        verbose_name='Оновлено'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Спроба розв’язання'
        verbose_name_plural = 'Спроби розв’язання'

    def __str__(self):
        return f'{self.task.title[:30]} — {self.user_id}'

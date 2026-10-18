from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from questions.models import Question

# The runner accepts up to 50 tests per run.
MAX_TEST_CASES = 50
# Languages the runner can execute; other tasks are listed but cannot be submitted.
SUPPORTED_LANGUAGES = {'python'}


class PracticeStatus(models.TextChoices):
    NOT_STARTED = 'not_started', 'Ще не виконане'
    FAILED = 'failed', 'Провалено'
    PARTIAL = 'partial', 'Частково правильно виконано'
    SUCCESS = 'success', 'Успішно'
    CHECKING = 'checking', 'Перевіряється'
    GENERATING = 'generating', 'Генерується'
    # The code was not checked (runner or queue unavailable); not the user's mistake.
    ERROR = 'error', 'Помилка перевірки'


class Difficulty(models.TextChoices):
    EASY = 'easy', 'Легка'
    MEDIUM = 'medium', 'Середня'
    HARD = 'hard', 'Складна'


class QuestionPractice(models.Model):
    """Завдання від AI до питання; користувач пише код із нуля."""

    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name='practices',
        verbose_name='Питання',
    )
    # Empty while the task is being generated.
    title = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Назва завдання'
    )
    description = models.TextField(blank=True, verbose_name='Умова завдання')
    difficulty = models.CharField(
        max_length=10,
        choices=Difficulty.choices,
        default=Difficulty.EASY,
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
    # Why the last generation or review did not happen; the review itself goes to feedback.
    error_message = models.TextField(blank=True, verbose_name='Пояснення помилки')
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Створено'
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Оновлено',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Практика до питання'
        verbose_name_plural = 'Практики до питань'

    def __str__(self):
        return self.title[:30] or f'Практика {self.pk}'

    @property
    def is_busy(self):
        """Generation or review is running; like a session run, a silent one is considered dead."""
        return (
            self.status in (PracticeStatus.GENERATING, PracticeStatus.CHECKING)
            and self.updated_at > timezone.now() - settings.GENERATION_STALE_AFTER
        )


class CodingTask(models.Model):
    """Окрема задача з шаблоном коду, без прив'язки до питання."""

    title = models.CharField(
        max_length=255,
        verbose_name='Назва задачі'
    )
    description = models.TextField(verbose_name='Умова задачі')
    difficulty = models.CharField(
        max_length=10,
        choices=Difficulty.choices,
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
        help_text=(
            'Список об’єктів {"input": "...", "expected_output": "...", "hidden": false}: '
            'input подається в stdin, вивід у stdout порівнюється з expected_output. '
            'Приховані тести (hidden: true) користувач не бачить.'
        ),
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

    def clean(self):
        super().clean()
        if not isinstance(self.test_cases, list) or not 1 <= len(self.test_cases) <= MAX_TEST_CASES:
            raise ValidationError({'test_cases': f'Потрібно від 1 до {MAX_TEST_CASES} тестів.'})
        for number, case in enumerate(self.test_cases, start=1):
            valid = (
                isinstance(case, dict)
                and set(case) <= {'input', 'expected_output', 'hidden'}
                and isinstance(case.get('input', ''), str)
                and isinstance(case.get('expected_output'), str)
                and isinstance(case.get('hidden', False), bool)
            )
            if not valid:
                raise ValidationError({
                    'test_cases': f'Тест {number}: потрібні рядки input і expected_output, hidden — true/false.',
                })

    @property
    def public_test_cases(self):
        return [case for case in self.test_cases if not case.get('hidden', False)]


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
    passed_tests = models.PositiveSmallIntegerField(default=0, verbose_name='Пройдено тестів')
    total_tests = models.PositiveSmallIntegerField(default=0, verbose_name='Усього тестів')
    # One entry per test; hidden tests keep only whether they passed.
    results = models.JSONField(default=list, blank=True, verbose_name='Результати тестів')
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

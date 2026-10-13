from django.db import models
from django.db.models import F

from interviews.models import Session

MAX_QUESTIONS_PER_SESSION = 50


class Question(models.Model):
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        verbose_name='Сесія',
        related_name='questions'
    )

    text = models.TextField(verbose_name='Питання')
    ai_answer = models.TextField(
        blank=True,
        null=True,
        verbose_name='Відповідь від АІ')
    # The same answer split into explanation / example / interview_answer / common_mistake;
    # ai_answer is these parts joined into one text. Null for answers generated before the split.
    ai_parts = models.JSONField(
        blank=True,
        null=True,
        verbose_name='Частини відповіді АІ')
    # Why the last run produced no answer for this question; empty when it succeeded.
    ai_error = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name='Помилка АІ')
    user_answer = models.TextField(
        blank=True,
        null=True,
        verbose_name='Редагована відповідь')

    order = models.PositiveIntegerField(
        default=1,
        verbose_name='Порядковий номер питання')

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Створено')

    class Meta:
        ordering = ['order']
        verbose_name = 'Питання'
        verbose_name_plural = 'Питання'
        constraints = [
            models.UniqueConstraint(fields=['session', 'order'], name='unique_question_order_in_session'),
        ]

    @property
    def final_answer(self):
        """The user's own answer wins over the AI one; '' is a deliberate empty answer."""
        return self.user_answer if self.user_answer is not None else self.ai_answer

    def __str__(self):
        return f'{self.session} - {self.text[:50]}'

def renumber(questions):
    """Give all questions of a session orders 1..n in the given sequence.

    The unique (session, order) constraint is checked row by row, so the rows first move above
    every current order and only then take their final places: no step repeats an order.
    """
    questions = list(questions)
    if not questions:
        return
    offset = max(question.order for question in questions) + 1
    Question.objects.filter(pk__in=[question.pk for question in questions]).update(order=F('order') + offset)
    for number, question in enumerate(questions, start=1):
        Question.objects.filter(pk=question.pk).update(order=number)
        question.order = number

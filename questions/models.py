from django.db import models

from interviews.models import Session


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
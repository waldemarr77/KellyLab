from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from interviews.models import Session
from questions.models import Question
from users.models import CustomUser

from .models import CodingAttempt, CodingTask, Difficulty, QuestionPractice


class PracticeAdminTests(TestCase):
    def setUp(self):
        self.admin = CustomUser.objects.create_superuser(
            username='admin', email='admin@example.com', password='Admin-password-517!',
        )
        self.client.force_login(self.admin)

    def add_rows(self, number):
        user = CustomUser.objects.create_user(username=f'user{number}', email=f'user{number}@example.com')
        session = Session.objects.create(user=user, name=f'Session {number}')
        question = Question.objects.create(session=session, text=f'Питання {number}', order=1)
        QuestionPractice.objects.create(question=question, title=f'Практика {number}', description='Умова')
        task = CodingTask.objects.create(
            title=f'Задача {number}', description='Умова', difficulty=Difficulty.EASY,
            language='python', skeleton='def solve(): ...',
        )
        CodingAttempt.objects.create(user=user, task=task, user_solution='def solve(): return 1')

    def count_queries(self, url):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_changelists_queries_do_not_grow_with_rows(self):
        urls = [f'/admin/practice/{model}/' for model in ('questionpractice', 'codingtask', 'codingattempt')]
        self.add_rows(1)
        one_row = [self.count_queries(url) for url in urls]
        self.add_rows(2)
        self.add_rows(3)
        self.assertEqual([self.count_queries(url) for url in urls], one_row)

    def test_change_pages_open(self):
        self.add_rows(1)
        for model, obj in (
            ('questionpractice', QuestionPractice.objects.get()),
            ('codingtask', CodingTask.objects.get()),
            ('codingattempt', CodingAttempt.objects.get()),
        ):
            with self.subTest(model=model):
                response = self.client.get(f'/admin/practice/{model}/{obj.pk}/change/')
                self.assertEqual(response.status_code, 200)

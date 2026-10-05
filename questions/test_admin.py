from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from interviews.models import Session
from users.models import CustomUser

from .models import Question


class QuestionAdminTests(TestCase):
    def setUp(self):
        self.admin = CustomUser.objects.create_superuser(
            username='admin', email='admin@example.com', password='Admin-password-517!',
        )
        self.client.force_login(self.admin)

    def add_session(self, number):
        user = CustomUser.objects.create_user(username=f'user{number}', email=f'user{number}@example.com')
        session = Session.objects.create(user=user, name=f'Session {number}')
        Question.objects.create(session=session, text=f'Питання {number}', order=1, ai_answer='Відповідь')
        return session

    def count_queries(self, url):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_changelist_queries_do_not_grow_with_rows(self):
        url = '/admin/questions/question/'
        self.add_session(1)
        one_row = self.count_queries(url)
        self.add_session(2)
        self.add_session(3)
        self.assertEqual(self.count_queries(url), one_row)

    def test_question_and_session_pages_open(self):
        session = self.add_session(1)
        question = session.questions.get()
        response = self.client.get(f'/admin/questions/question/{question.pk}/change/')
        self.assertContains(response, 'Питання 1')
        # The session page lists its questions inline.
        response = self.client.get(f'/admin/interview_sessions/session/{session.pk}/change/')
        self.assertContains(response, 'Питання 1')
        self.assertEqual(self.client.get('/admin/questions/question/?q=Питання').status_code, 200)

import threading
import time
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from exports.models import Export
from questions.models import Question
from users.models import CustomUser

from . import quota, views
from .models import AIUsage, GlobalAIUsage, Session

# Long enough for the second request to read the row while the first one is still in its transaction.
RACE_DELAY_SECONDS = 0.3


def slowed(func):
    """Wrap func so the caller lingers after it, keeping the race window open."""
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        time.sleep(RACE_DELAY_SECONDS)
        return result
    return wrapper


# SQLite ignores select_for_update and serialises writes, so the races only exist on PostgreSQL.
@skipUnless(connection.vendor == 'postgresql', 'Concurrency tests need PostgreSQL row locks')
@override_settings(AI_DAILY_QUESTION_LIMIT=100, AI_GLOBAL_DAILY_QUESTION_LIMIT=1000)
class ConcurrentRequestTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.user = CustomUser.objects.create_user(
            username='owner', email='owner@example.com', email_verified=True,
        )
        self.session = self.make_session(questions=2)
        # Only the publishing is mocked: a real worker must not run Gemini or render files here.
        for task in (views.generate_session_answers, views.generate_export):
            publish = patch.object(task, 'apply_async')
            self.addCleanup(publish.stop)
            publish.start()

    def make_session(self, questions):
        session = Session.objects.create(user=self.user, name='Python Junior')
        Question.objects.bulk_create(
            Question(session=session, text=f'Питання {order}?', order=order) for order in range(1, questions + 1)
        )
        return session

    def race(self, *requests):
        """Send the requests from separate threads at the same moment; return the status codes."""
        barrier = threading.Barrier(len(requests))
        results = [None] * len(requests)

        def send(index, method, url, data):
            client = APIClient()
            client.force_authenticate(self.user)
            try:
                barrier.wait()
                results[index] = getattr(client, method)(url, data, format='json').status_code
            except Exception as error:  # noqa: BLE001 - reported by the assertion below
                results[index] = error
            finally:
                # Each thread opens its own connection; an open one would block the test database teardown.
                connection.close()

        threads = [threading.Thread(target=send, args=(index, *request)) for index, request in enumerate(requests)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return sorted(results, key=str)

    def test_double_generate_starts_one_run(self):
        url = f'/api/sessions/{self.session.pk}/generate/'
        with patch.object(Session, 'is_busy', property(slowed(Session.is_busy.fget))):
            codes = self.race(('post', url, {}), ('post', url, {}))
        self.assertEqual(codes, [202, 409])
        self.assertEqual(views.generate_session_answers.apply_async.call_count, 1)
        self.assertEqual(AIUsage.objects.get(user=self.user, date=timezone.localdate()).questions, 2)

    def test_double_export_creates_one_export(self):
        url = f'/api/sessions/{self.session.pk}/exports/'
        with patch.object(views, 'reusable_export', slowed(views.reusable_export)):
            codes = self.race(('post', url, {'format': 'pdf'}), ('post', url, {'format': 'pdf'}))
        # The second request waits for the first and then reuses its export.
        self.assertEqual(codes, [200, 202])
        self.assertEqual(Export.objects.filter(session=self.session).count(), 1)
        self.assertEqual(views.generate_export.apply_async.call_count, 1)

    @override_settings(AI_DAILY_QUESTION_LIMIT=3)
    def test_quota_on_the_limit_lets_one_run_through(self):
        # Different sessions take different session locks, so only the quota lock keeps them apart.
        other = self.make_session(questions=2)
        # Existing counters: otherwise the unique index on the first insert serialises the runs by itself.
        AIUsage.objects.create(user=self.user, date=timezone.localdate())
        GlobalAIUsage.objects.create(date=timezone.localdate(timezone=settings.GEMINI_QUOTA_TIMEZONE))
        with patch.object(quota, 'locked_usage', slowed(quota.locked_usage)):
            codes = self.race(
                ('post', f'/api/sessions/{self.session.pk}/generate/', {}),
                ('post', f'/api/sessions/{other.pk}/generate/', {}),
            )
        self.assertEqual(codes, [202, 429])
        self.assertEqual(views.generate_session_answers.apply_async.call_count, 1)
        self.assertEqual(AIUsage.objects.get(user=self.user, date=timezone.localdate()).questions, 2)

    def test_double_import_into_empty_session_imports_once(self):
        empty = self.make_session(questions=0)
        url = f'/api/sessions/{empty.pk}/import/'
        data = {'text': 'Що таке ORM?\nЩо таке транзакція?'}
        with patch.object(Session, 'is_busy', property(slowed(Session.is_busy.fget))):
            codes = self.race(('post', url, data), ('post', url, data))
        self.assertEqual(codes, [201, 400])
        self.assertEqual(empty.questions.count(), 2)

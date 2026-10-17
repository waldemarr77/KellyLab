from datetime import timedelta
from unittest.mock import patch

import httpx
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from users.models import CustomUser

from .models import CodingAttempt, CodingTask, Difficulty, PracticeStatus
from .tasks import same_output

TEST_CASES = [
    {'input': '1 2\n', 'expected_output': '3\n'},
    {'input': '10 5\n', 'expected_output': '15\n', 'hidden': True},
]


def run(stdout='', status='ok', stderr=''):
    return {'status': status, 'exit_code': 0 if status == 'ok' else 1, 'stdout': stdout, 'stderr': stderr}


def runner_reply(*runs, status_code=200):
    return httpx.Response(status_code, json={'results': list(runs)}, request=httpx.Request('POST', 'http://runner/run'))


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, RUNNER_URL='http://runner')
class CodingTaskApiTests(APITestCase):
    def setUp(self):
        cache.clear()  # Throttle counters would otherwise carry over between tests.
        self.user = CustomUser.objects.create_user(username='coder', email='coder@example.com')
        self.other = CustomUser.objects.create_user(username='other', email='other@example.com')
        self.client.force_authenticate(self.user)
        self.task = CodingTask.objects.create(
            title='Сума', description='Виведи суму двох чисел.', difficulty=Difficulty.EASY,
            language='python', skeleton='a, b = map(int, input().split())\n', test_cases=TEST_CASES,
        )
        runner_patch = patch('practice.tasks.httpx.post', return_value=runner_reply(run('3\n'), run('15\n')))
        self.runner = runner_patch.start()
        self.addCleanup(runner_patch.stop)
        self.url = f'/api/coding-tasks/{self.task.pk}/attempts/'

    def submit(self, code='print(sum(map(int, input().split())))'):
        return self.client.post(self.url, {'code': code}, format='json')

    def test_list_and_detail_hide_hidden_tests(self):
        listed = self.client.get('/api/coding-tasks/')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data['results'][0], {
            'id': self.task.pk, 'title': 'Сума', 'difficulty': 'easy', 'language': 'python',
        })
        detail = self.client.get(f'/api/coding-tasks/{self.task.pk}/')
        self.assertEqual(detail.data['examples'], [{'input': '1 2\n', 'expected_output': '3\n'}])
        self.assertEqual(detail.data['tests_count'], 2)
        self.assertNotIn('15', str(detail.data))
        self.assertEqual(self.client.get('/api/coding-tasks/?difficulty=hard').data['count'], 0)

    def test_tasks_require_login(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/coding-tasks/').status_code, 401)

    def test_correct_solution_passes_every_test(self):
        created = self.submit()
        self.assertEqual(created.status_code, 202)
        self.assertEqual(created.data['status'], PracticeStatus.SUCCESS)
        self.assertEqual((created.data['passed_tests'], created.data['total_tests']), (2, 2))
        self.assertEqual(created.data['feedback'], 'Пройдено 2 з 2 тестів.')
        # Only the input goes to the runner; the expected output stays here.
        payload = self.runner.call_args.kwargs['json']
        self.assertEqual(payload['tests'], [{'input': '1 2\n'}, {'input': '10 5\n'}])
        self.assertEqual(payload['language'], 'python')

    def test_hidden_test_details_are_not_returned(self):
        self.runner.return_value = runner_reply(run('3\n'), run('999\n', stderr='10 5'))
        created = self.submit()
        self.assertEqual(created.data['status'], PracticeStatus.PARTIAL)
        public, hidden = created.data['results']
        self.assertEqual(public['status'], 'passed')
        self.assertEqual(public['output'], '3\n')
        self.assertEqual(hidden, {'number': 2, 'status': 'wrong_answer', 'passed': False, 'hidden': True})

    def test_errors_and_timeouts_fail_the_attempt(self):
        self.runner.return_value = runner_reply(
            run(status='error', stderr='ZeroDivisionError: division by zero'), run(status='timeout'),
        )
        created = self.submit('print(1 / 0)')
        self.assertEqual(created.data['status'], PracticeStatus.FAILED)
        self.assertEqual([result['status'] for result in created.data['results']], ['error', 'timeout'])
        self.assertIn('ZeroDivisionError', created.data['results'][0]['error'])

    def test_runner_failure_marks_attempt_error(self):
        replies = {
            'unavailable': httpx.ConnectError('refused'),
            'server error': runner_reply(status_code=500),
            'wrong number of results': runner_reply(run('3\n')),
        }
        for name, reply in replies.items():
            with self.subTest(name):
                CodingAttempt.objects.all().delete()
                self.runner.side_effect = reply if isinstance(reply, Exception) else None
                self.runner.return_value = reply
                created = self.submit()
                self.assertEqual(created.data['status'], PracticeStatus.ERROR)
                self.assertEqual(created.data['results'], [])

    def test_one_attempt_is_checked_at_a_time(self):
        CodingAttempt.objects.create(user=self.user, task=self.task, status=PracticeStatus.CHECKING)
        self.assertEqual(self.submit().status_code, 409)
        # A check that died long ago does not block the user forever.
        CodingAttempt.objects.update(updated_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(self.submit().status_code, 202)

    def test_unsupported_task_and_bad_code_are_rejected(self):
        self.assertEqual(self.submit('x' * 20_001).status_code, 400)
        self.assertEqual(self.client.post(self.url, {}, format='json').status_code, 400)
        self.task.language = 'javascript'
        self.task.save()
        self.assertEqual(self.submit().status_code, 400)
        self.assertFalse(CodingAttempt.objects.exists())
        self.runner.assert_not_called()

    def test_attempts_are_private(self):
        attempt = CodingAttempt.objects.create(user=self.other, task=self.task, user_solution='secret')
        self.assertEqual(self.client.get(f'/api/coding-attempts/{attempt.pk}/').status_code, 404)
        self.submit()
        mine = self.client.get(f'/api/coding-attempts/?task={self.task.pk}')
        self.assertEqual(mine.data['count'], 1)
        self.assertNotIn('secret', str(mine.data))

    def test_submissions_are_throttled(self):
        statuses = [self.submit().status_code for _ in range(11)]
        self.assertEqual(statuses[:10], [202] * 10)
        self.assertEqual(statuses[10], 429)


class TestCasesValidationTests(TestCase):
    def task(self, test_cases):
        return CodingTask(
            title='T', description='D', difficulty=Difficulty.EASY, language='python', skeleton='pass',
            test_cases=test_cases,
        )

    def test_valid_cases_pass(self):
        self.task(TEST_CASES).full_clean()

    def test_invalid_cases_are_rejected(self):
        for cases in ([], [{'input': '1'}], [{'input': 1, 'expected_output': '1'}],
                      [{'expected_output': '1', 'hidden': 'yes'}], [{'expected_output': '1', 'extra': 1}], {}):
            with self.subTest(cases=cases), self.assertRaises(ValidationError):
                self.task(cases).full_clean()


class OutputComparisonTests(TestCase):
    def test_trailing_whitespace_is_ignored(self):
        self.assertTrue(same_output('3  \n\n', '3'))
        self.assertTrue(same_output('1\r\n2\r\n', '1\n2\n'))
        self.assertFalse(same_output('3 4', '34'))
        self.assertFalse(same_output('', '0'))

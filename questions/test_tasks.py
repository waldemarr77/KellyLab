from unittest.mock import Mock, patch

import httpx
from celery.exceptions import SoftTimeLimitExceeded
from django.test import SimpleTestCase, override_settings
from google.genai import errors as genai_errors

from .tasks import generate_with_retries, is_transient


def api_error(code):
    error_class = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return error_class(code, {'error': {'code': code, 'message': 'error'}})


TRANSIENT_ERRORS = [
    api_error(408), api_error(429), api_error(500), api_error(503),
    httpx.ReadTimeout('timeout'), httpx.ConnectError('connection refused'), httpx.ReadError('connection reset'),
]
PERMANENT_ERRORS = [
    api_error(400), api_error(401), api_error(403), api_error(404),
    ValueError('The model returned an empty response'), SoftTimeLimitExceeded(),
]


class IsTransientTests(SimpleTestCase):
    def test_transient_errors(self):
        for error in TRANSIENT_ERRORS:
            with self.subTest(error=repr(error)):
                self.assertTrue(is_transient(error))

    def test_permanent_errors(self):
        for error in PERMANENT_ERRORS:
            with self.subTest(error=repr(error)):
                self.assertFalse(is_transient(error))


@override_settings(AI_MAX_ATTEMPTS=3, AI_RETRY_DELAY_SECONDS=2)
@patch('questions.tasks.time.sleep')
class GenerateWithRetriesTests(SimpleTestCase):
    def test_transient_error_is_retried_until_success(self, sleep):
        for error in TRANSIENT_ERRORS:
            with self.subTest(error=repr(error)):
                sleep.reset_mock()
                generate = Mock(side_effect=[error, error, 'Answer'])
                self.assertEqual(generate_with_retries(generate, 'Question'), 'Answer')
                self.assertEqual(generate.call_count, 3)
                generate.assert_called_with('Question')
                self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4])

    def test_permanent_error_fails_without_retry(self, sleep):
        for error in PERMANENT_ERRORS:
            with self.subTest(error=repr(error)):
                sleep.reset_mock()
                generate = Mock(side_effect=error)
                with self.assertRaises(type(error)) as raised:
                    generate_with_retries(generate, 'Question')
                self.assertIs(raised.exception, error)
                self.assertEqual(generate.call_count, 1)
                sleep.assert_not_called()

    def test_transient_error_is_raised_after_last_attempt(self, sleep):
        error = api_error(503)
        generate = Mock(side_effect=error)
        with self.assertRaises(genai_errors.ServerError) as raised:
            generate_with_retries(generate, 'Question')
        self.assertIs(raised.exception, error)
        self.assertEqual(generate.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_permanent_error_after_transient_stops_retries(self, sleep):
        generate = Mock(side_effect=[api_error(429), api_error(401), 'Answer'])
        with self.assertRaises(genai_errors.ClientError):
            generate_with_retries(generate, 'Question')
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

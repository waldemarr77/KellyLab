import logging

import httpx
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import CodingAttempt, PracticeStatus

logger = logging.getLogger(__name__)


def same_output(actual, expected):
    """Trailing spaces and blank lines at the end do not matter, as on most judges."""
    def normalize(text):
        return [line.rstrip() for line in text.rstrip().splitlines()]
    return normalize(actual) == normalize(expected)


def test_result(number, case, run):
    if run['status'] == 'ok':
        status = 'passed' if same_output(run['stdout'], case['expected_output']) else 'wrong_answer'
    else:
        status = run['status']  # 'error' or 'timeout'
    result = {'number': number, 'status': status, 'passed': status == 'passed'}
    if case.get('hidden', False):
        # Input, expected and actual output of a hidden test would reveal the test itself.
        result['hidden'] = True
    else:
        result.update({
            'hidden': False,
            'input': case.get('input', ''),
            'expected_output': case['expected_output'],
            'output': run['stdout'][:2000],
            'error': run['stderr'][-2000:],
        })
    return result


@shared_task(soft_time_limit=settings.RUNNER_TIMEOUT_SECONDS + 30, time_limit=settings.RUNNER_TIMEOUT_SECONDS + 60)
def check_attempt(attempt_id):
    attempt = CodingAttempt.objects.select_related('task').filter(pk=attempt_id).first()
    if attempt is None:
        return
    # The account can be deleted meanwhile, so the row is changed with update(), never save().
    row = CodingAttempt.objects.filter(pk=attempt_id, status=PracticeStatus.CHECKING)
    cases = attempt.task.test_cases
    try:
        response = httpx.post(
            f'{settings.RUNNER_URL}/run',
            json={
                'language': 'python',
                'code': attempt.user_solution,
                'tests': [{'input': case.get('input', '')} for case in cases],
                'time_limit': settings.CODE_TIME_LIMIT_SECONDS,
            },
            timeout=settings.RUNNER_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        runs = response.json()['results']
        if len(runs) != len(cases):
            raise ValueError('runner returned a different number of results')
        results = [test_result(number, case, run) for number, (case, run) in enumerate(zip(cases, runs), start=1)]
    except Exception as exc:  # noqa: BLE001 -- any failure (runner, network, time limit) must end the check
        logger.warning('Coding attempt %s was not checked: %s', attempt_id, type(exc).__name__)
        row.update(
            status=PracticeStatus.ERROR,
            feedback='Не вдалося перевірити рішення. Спробуйте ще раз пізніше.',
            updated_at=timezone.now(),
        )
        return
    passed = sum(result['passed'] for result in results)
    if passed == len(results):
        status = PracticeStatus.SUCCESS
    elif passed:
        status = PracticeStatus.PARTIAL
    else:
        status = PracticeStatus.FAILED
    row.update(
        status=status,
        passed_tests=passed,
        total_tests=len(results),
        results=results,
        feedback=f'Пройдено {passed} з {len(results)} тестів.',
        updated_at=timezone.now(),
    )

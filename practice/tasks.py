import logging

import httpx
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from interviews.quota import refund_daily_quota
from questions.ai import BadAIResponse
from questions.tasks import describe_error, generate_with_retries, throttled

from .ai import solution_reviewer, task_generator
from .models import CodingAttempt, PracticeStatus, QuestionPractice

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


def run_practice_ai(practice_id, status, charge, call):
    """Shared frame of the two AI tasks: claim the row, call Gemini once, store the outcome.

    call(practice, user, send) makes the request through send(), which marks it as sent, and returns
    the fields to save. The quota was charged at launch; it is refunded only if nothing was sent.
    """
    # The row is changed with update(), never save(): the practice can be deleted at any moment.
    active = QuestionPractice.objects.filter(pk=practice_id, status=status)
    practice = active.select_related('question__session__user').first()
    if practice is None:
        # No refund: a redelivered copy of a finished task also lands here, and its request was sent.
        return
    # It may have waited in the queue; mark it alive before the possibly slow request.
    if not active.update(updated_at=timezone.now()):
        return
    sent = False

    def send(*args):
        nonlocal sent
        sent = True
        return generate_with_retries(*args)

    try:
        fields = call(practice, practice.question.session.user, send)
    except BadAIResponse as exc:
        logger.warning('No usable AI reply for practice %s: %s', practice_id, describe_error(exc))
        fields = {'status': PracticeStatus.ERROR, 'error_message': 'AI не дав придатної відповіді. Спробуй ще раз.'}
    except Exception as exc:  # noqa: BLE001 -- task boundary must persist failed state for any provider failure
        # Never the provider's message: it can contain credentials or private input.
        logger.warning('Practice %s AI request failed: %s', practice_id, describe_error(exc))
        fields = {
            'status': PracticeStatus.ERROR,
            'error_message': 'Не вдалося звернутися до AI. Перевір налаштування AI та повтори.',
        }
    finally:
        if charge and not sent:
            refund_daily_quota(charge, charge['questions'])
    active.update(**fields, updated_at=timezone.now())


# Stay well below the Redis visibility timeout (1 h), as the session generation task does.
@shared_task(soft_time_limit=10 * 60, time_limit=11 * 60)
def generate_practice(practice_id, difficulty=None, charge=None):
    def call(practice, user, send):
        with task_generator(user.target_position, user.experience_level) as generate:
            task = send(throttled(generate), practice.question.text, difficulty)
        return {
            'title': task['title'][:255],
            'description': task['description'],
            # A difficulty the user asked for wins over the model's choice.
            'difficulty': difficulty or task['difficulty'],
            'status': PracticeStatus.NOT_STARTED,
            'error_message': '',
        }

    run_practice_ai(practice_id, PracticeStatus.GENERATING, charge, call)


@shared_task(soft_time_limit=10 * 60, time_limit=11 * 60)
def review_solution(practice_id, charge=None):
    def call(practice, user, send):
        with solution_reviewer(user.target_position, user.experience_level) as review:
            result = send(
                throttled(review), practice.question.text, practice.title, practice.description,
                practice.user_solution,
            )
        return {'status': result['status'], 'feedback': result['feedback'], 'error_message': ''}

    run_practice_ai(practice_id, PracticeStatus.CHECKING, charge, call)

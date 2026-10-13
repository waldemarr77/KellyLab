import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from interviews.models import AIUsage, Session
from questions.models import Question

from .models import CustomUser


class AuthenticationTests(APITestCase):
    credentials = {'email': 'candidate@example.com', 'password': 'A-strong-password-874!'}

    def setUp(self):
        # Throttle counters live in the cache and would leak between tests.
        cache.clear()

    def test_registration_login_refresh_and_profile(self):
        response = self.client.post('/api/auth/register/', self.credentials)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(list(response.data), ['detail'])
        user = CustomUser.objects.get(email=self.credentials['email'])
        self.assertTrue(user.check_password(self.credentials['password']))
        token = self.client.post('/api/auth/token/', {**self.credentials, 'email': 'CANDIDATE@example.com'})
        self.assertEqual(token.status_code, 200)
        refreshed = self.client.post('/api/auth/token/refresh/', {'refresh': token.data['refresh']})
        self.assertEqual(refreshed.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refreshed.data["access"]}')
        profile = self.client.patch('/api/auth/me/', {
            'target_position': 'Python Developer', 'experience_level': 'junior', 'is_staff': True,
        })
        self.assertEqual(profile.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.experience_level, 'junior')
        self.assertFalse(user.is_staff)

    def login(self):
        self.client.post('/api/auth/register/', self.credentials)
        return self.client.post('/api/auth/token/', self.credentials).data['refresh']

    def test_logout_revokes_refresh_token(self):
        refresh = self.login()
        logout = self.client.post('/api/auth/logout/', {'refresh': refresh})
        self.assertEqual(logout.status_code, 200)
        refreshed = self.client.post('/api/auth/token/refresh/', {'refresh': refresh})
        self.assertEqual(refreshed.status_code, 401)

    def test_rotation_revokes_previous_refresh_token(self):
        old = self.login()
        rotated = self.client.post('/api/auth/token/refresh/', {'refresh': old})
        self.assertEqual(rotated.status_code, 200)
        self.assertNotEqual(rotated.data['refresh'], old)
        reused = self.client.post('/api/auth/token/refresh/', {'refresh': old})
        self.assertEqual(reused.status_code, 401)
        fresh = self.client.post('/api/auth/token/refresh/', {'refresh': rotated.data['refresh']})
        self.assertEqual(fresh.status_code, 200)

    def test_logout_rejects_invalid_token(self):
        result = self.client.post('/api/auth/logout/', {'refresh': 'not-a-token'})
        self.assertEqual(result.status_code, 401)

    def test_duplicate_email_gets_the_same_answer_and_a_letter(self):
        first = self.client.post('/api/auth/register/', self.credentials)
        duplicate = self.client.post('/api/auth/register/', {
            'email': 'CANDIDATE@example.com', 'password': 'Another-strong-password-519!',
        })
        self.assertEqual((duplicate.status_code, duplicate.data), (first.status_code, first.data))
        self.assertEqual(CustomUser.objects.count(), 1)
        user = CustomUser.objects.get()
        self.assertTrue(user.check_password(self.credentials['password']))
        self.assertEqual([letter.to for letter in mail.outbox], [[user.email], [user.email]])
        self.assertIn('уже існує', mail.outbox[1].body)

    def test_weak_password(self):
        weak = self.client.post('/api/auth/register/', {'email': 'new@example.com', 'password': '123'})
        self.assertEqual(weak.status_code, 400)

    def test_invalid_credentials(self):
        self.client.post('/api/auth/register/', self.credentials)
        result = self.client.post('/api/auth/token/', {**self.credentials, 'password': 'wrong'})
        self.assertEqual(result.status_code, 401)

    def wrong(self, number):
        return {'email': f'user{number}@example.com', 'password': 'wrong'}

    def test_login_attempts_are_throttled(self):
        # A new email each time, so only the IP throttle (not the per-email lock) applies.
        statuses = [self.client.post('/api/auth/token/', self.wrong(i)).status_code for i in range(11)]
        self.assertEqual(statuses[:10], [401] * 10)
        self.assertEqual(statuses[10], 429)

    def test_spoofed_forwarded_for_does_not_reset_throttle(self):
        statuses = [
            self.client.post('/api/auth/token/', self.wrong(i), HTTP_X_FORWARDED_FOR=f'203.0.113.{i}').status_code
            for i in range(11)
        ]
        self.assertEqual(statuses[10], 429)

    def test_behind_proxy_only_proxy_appended_address_counts(self):
        with override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, 'NUM_PROXIES': 1}):
            statuses = [
                # The client controls the left part; our proxy appends the real address on the right.
                self.client.post(
                    '/api/auth/token/', self.wrong(i), HTTP_X_FORWARDED_FOR=f'198.51.100.{i}, 203.0.113.7',
                ).status_code
                for i in range(11)
            ]
        self.assertEqual(statuses[10], 429)

    def test_token_refresh_is_throttled(self):
        statuses = [self.client.post('/api/auth/token/refresh/', {'refresh': 'bad'}).status_code for _ in range(31)]
        self.assertEqual(statuses[:30], [401] * 30)
        self.assertEqual(statuses[30], 429)


class EmailVerificationTests(APITestCase):
    credentials = {'email': 'candidate@example.com', 'password': 'A-strong-password-874!'}

    def setUp(self):
        cache.clear()
        self.client.post('/api/auth/register/', self.credentials)
        self.user = CustomUser.objects.get()

    def link_params(self, letter):
        return dict(re.findall(r'(uid|token)=([^&\s]+)', letter.body))

    def test_new_account_is_unverified_until_the_link_is_used(self):
        self.assertFalse(self.user.email_verified)
        params = self.link_params(mail.outbox[0])
        self.assertEqual(self.client.post('/api/auth/verify-email/', params).status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        # The token depends on the verification state, so it works only once.
        self.assertEqual(self.client.post('/api/auth/verify-email/', params).status_code, 400)

    def test_invalid_link_is_rejected(self):
        params = self.link_params(mail.outbox[0])
        for data in [{**params, 'token': 'bad'}, {**params, 'uid': 'bad'}, {'uid': 'MTAw', 'token': params['token']}]:
            with self.subTest(data=data):
                self.assertEqual(self.client.post('/api/auth/verify-email/', data).status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    def test_profile_shows_status_and_letter_can_be_resent(self):
        self.client.force_authenticate(self.user)
        self.assertFalse(self.client.get('/api/auth/me/').data['email_verified'])
        statuses = [self.client.post('/api/auth/verify-email/resend/').status_code for _ in range(4)]
        self.assertEqual(statuses, [202, 202, 202, 429])
        self.assertEqual(len(mail.outbox), 4)
        self.client.post('/api/auth/verify-email/', self.link_params(mail.outbox[-1]))
        self.user.refresh_from_db()
        cache.clear()
        self.assertEqual(self.client.post('/api/auth/verify-email/resend/').status_code, 400)


class LoginLimitTests(APITestCase):
    credentials = {'email': 'candidate@example.com', 'password': 'A-strong-password-874!'}

    def setUp(self):
        cache.clear()
        CustomUser.objects.create_user(username='candidate', **self.credentials)

    def test_failures_from_many_ips_lock_the_email(self):
        wrong = {**self.credentials, 'password': 'wrong'}
        statuses = [
            self.client.post('/api/auth/token/', wrong, REMOTE_ADDR=f'203.0.113.{i}').status_code for i in range(6)
        ]
        self.assertEqual(statuses, [401] * 5 + [429])
        # Even the right password is not checked while the email is locked.
        self.assertEqual(self.client.post('/api/auth/token/', self.credentials, REMOTE_ADDR='198.51.100.1').status_code, 429)
        other = self.client.post('/api/auth/token/', {**self.credentials, 'email': 'other@example.com'})
        self.assertEqual(other.status_code, 401)

    @override_settings(LOGIN_FAILURE_LIMIT_PER_EMAIL=100)
    def test_failures_for_many_emails_lock_the_ip(self):
        wrong = {**self.credentials, 'password': 'wrong'}
        for i in range(20):
            self.client.post('/admin/login/', {'username': f'user{i}@example.com', 'password': 'wrong'})
        self.assertEqual(self.client.post('/api/auth/token/', self.credentials).status_code, 429)
        self.assertEqual(self.client.post('/api/auth/token/', wrong, REMOTE_ADDR='198.51.100.1').status_code, 401)


class AdminLoginLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_superuser(
            username='admin', email='admin@example.com', password='Admin-password-913!',
        )

    def login(self, password):
        return self.client.post('/admin/login/?next=/admin/', {'username': 'admin@example.com', 'password': password})

    def test_admin_login_is_locked_after_failures(self):
        for _ in range(5):
            self.assertEqual(self.login('wrong').status_code, 200)
        locked = self.login('Admin-password-913!')
        self.assertEqual(locked.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        cache.clear()
        self.assertEqual(self.login('Admin-password-913!').status_code, 302)


class PasswordAndAccountTests(APITestCase):
    password = 'A-strong-password-874!'
    new_password = 'Another-strong-password-519!'

    def setUp(self):
        cache.clear()
        self.user = CustomUser.objects.create_user(
            username='candidate', email='candidate@example.com', password=self.password,
        )

    def login(self, password=None):
        return self.client.post('/api/auth/token/', {'email': self.user.email, 'password': password or self.password})

    def authenticate(self):
        tokens = self.login().data
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {tokens["access"]}')
        return tokens['refresh']

    def link_params(self, letter):
        return dict(re.findall(r'(uid|token)=([^&\s]+)', letter.body))

    def test_change_password_checks_old_one_and_logs_out_everywhere(self):
        refresh = self.authenticate()
        wrong = self.client.post('/api/auth/password/change/', {'password': 'wrong', 'new_password': self.new_password})
        self.assertEqual(wrong.status_code, 400)
        weak = self.client.post('/api/auth/password/change/', {'password': self.password, 'new_password': '123'})
        self.assertEqual(weak.status_code, 400)
        changed = self.client.post(
            '/api/auth/password/change/', {'password': self.password, 'new_password': self.new_password},
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.post('/api/auth/token/refresh/', {'refresh': refresh}).status_code, 401)
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(self.login(self.new_password).status_code, 200)

    def test_change_password_requires_login(self):
        result = self.client.post('/api/auth/password/change/', {'password': self.password, 'new_password': self.new_password})
        self.assertEqual(result.status_code, 401)

    def test_reset_answer_does_not_reveal_accounts(self):
        known = self.client.post('/api/auth/password/reset/', {'email': 'CANDIDATE@example.com'})
        unknown = self.client.post('/api/auth/password/reset/', {'email': 'nobody@example.com'})
        self.assertEqual((known.status_code, known.data), (unknown.status_code, unknown.data))
        self.assertEqual(known.status_code, 202)
        self.assertEqual([letter.to for letter in mail.outbox], [[self.user.email]])

    def test_reset_link_sets_new_password_once(self):
        refresh = self.authenticate()
        self.client.credentials()
        self.client.post('/api/auth/password/reset/', {'email': self.user.email})
        params = self.link_params(mail.outbox[0])
        weak = self.client.post('/api/auth/password/reset/confirm/', {**params, 'new_password': '123'})
        self.assertEqual(weak.status_code, 400)
        confirm = {**params, 'new_password': self.new_password}
        self.assertEqual(self.client.post('/api/auth/password/reset/confirm/', confirm).status_code, 200)
        self.assertEqual(self.login(self.new_password).status_code, 200)
        # The token hashes the password, so the same link no longer works.
        again = self.client.post('/api/auth/password/reset/confirm/', {**params, 'new_password': 'Third-password-306!'})
        self.assertEqual(again.status_code, 400)
        self.assertEqual(self.client.post('/api/auth/token/refresh/', {'refresh': refresh}).status_code, 401)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_reset_rejects_bad_links(self):
        self.client.post('/api/auth/password/reset/', {'email': self.user.email})
        params = self.link_params(mail.outbox[0])
        for data in [{**params, 'token': 'bad'}, {**params, 'uid': 'bad'}]:
            with self.subTest(data=data):
                response = self.client.post('/api/auth/password/reset/confirm/', {**data, 'new_password': self.new_password})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.login().status_code, 200)

    def test_reset_endpoints_use_auth_throttle(self):
        statuses = [
            self.client.post('/api/auth/password/reset/', {'email': f'user{i}@example.com'}).status_code for i in range(11)
        ]
        self.assertEqual(statuses[10], 429)
        cache.clear()
        statuses = [
            self.client.post('/api/auth/password/reset/confirm/', {'uid': 'x', 'token': 'x', 'new_password': 'x'}).status_code
            for _ in range(11)
        ]
        self.assertEqual(statuses[10], 429)

    def test_account_deletion_requires_password_and_removes_data(self):
        refresh = self.authenticate()
        session = Session.objects.create(user=self.user, name='S')
        Question.objects.create(session=session, text='Q')
        AIUsage.objects.create(user=self.user, date='2026-10-14', questions=3)
        self.assertEqual(self.client.delete('/api/auth/me/', {'password': 'wrong'}, format='json').status_code, 400)
        self.assertEqual(self.client.delete('/api/auth/me/').status_code, 400)
        self.assertTrue(CustomUser.objects.filter(pk=self.user.pk).exists())
        deleted = self.client.delete('/api/auth/me/', {'password': self.password}, format='json')
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(CustomUser.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Session.objects.exists())
        self.assertFalse(Question.objects.exists())
        self.assertFalse(AIUsage.objects.exists())
        self.assertEqual(self.client.post('/api/auth/token/refresh/', {'refresh': refresh}).status_code, 401)

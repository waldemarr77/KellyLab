from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from .models import CustomUser


class AuthenticationTests(APITestCase):
    credentials = {'email': 'candidate@example.com', 'password': 'A-strong-password-874!'}

    def setUp(self):
        # Throttle counters live in the cache and would leak between tests.
        cache.clear()

    def test_registration_login_refresh_and_profile(self):
        response = self.client.post('/api/auth/register/', self.credentials)
        self.assertEqual(response.status_code, 201)
        self.assertNotIn('password', response.data)
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

    def test_duplicate_email_case_and_weak_password(self):
        self.client.post('/api/auth/register/', self.credentials)
        duplicate = self.client.post('/api/auth/register/', {**self.credentials, 'email': 'CANDIDATE@example.com'})
        self.assertEqual(duplicate.status_code, 400)
        weak = self.client.post('/api/auth/register/', {'email': 'new@example.com', 'password': '123'})
        self.assertEqual(weak.status_code, 400)

    def test_invalid_credentials(self):
        self.client.post('/api/auth/register/', self.credentials)
        result = self.client.post('/api/auth/token/', {**self.credentials, 'password': 'wrong'})
        self.assertEqual(result.status_code, 401)

    def test_login_attempts_are_throttled(self):
        wrong = {**self.credentials, 'password': 'wrong'}
        statuses = [self.client.post('/api/auth/token/', wrong).status_code for _ in range(11)]
        self.assertEqual(statuses[:10], [401] * 10)
        self.assertEqual(statuses[10], 429)

    def test_spoofed_forwarded_for_does_not_reset_throttle(self):
        wrong = {**self.credentials, 'password': 'wrong'}
        statuses = [
            self.client.post('/api/auth/token/', wrong, HTTP_X_FORWARDED_FOR=f'203.0.113.{i}').status_code
            for i in range(11)
        ]
        self.assertEqual(statuses[10], 429)

    def test_behind_proxy_only_proxy_appended_address_counts(self):
        wrong = {**self.credentials, 'password': 'wrong'}
        with override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, 'NUM_PROXIES': 1}):
            statuses = [
                # The client controls the left part; our proxy appends the real address on the right.
                self.client.post(
                    '/api/auth/token/', wrong, HTTP_X_FORWARDED_FOR=f'198.51.100.{i}, 203.0.113.7',
                ).status_code
                for i in range(11)
            ]
        self.assertEqual(statuses[10], 429)

    def test_token_refresh_is_throttled(self):
        statuses = [self.client.post('/api/auth/token/refresh/', {'refresh': 'bad'}).status_code for _ in range(31)]
        self.assertEqual(statuses[:30], [401] * 30)
        self.assertEqual(statuses[30], 429)

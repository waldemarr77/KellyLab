from rest_framework.test import APITestCase

from .models import CustomUser


class AuthenticationTests(APITestCase):
    credentials = {'email': 'candidate@example.com', 'password': 'A-strong-password-874!'}

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

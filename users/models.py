from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models


class CustomUserManager(UserManager):
    @classmethod
    def normalize_email(cls, email):
        # Login lowercases the email, so it is stored the same way: admin, createsuperuser and the API alike.
        return (email or '').strip().lower()


class CustomUser(AbstractUser):
    LEVEL_CHOICES = [
        ('trainee', 'Trainee'),
        ('junior', 'Junior'),
        ('middle', 'Middle'),
        ('senior', 'Senior'),
    ]

    email = models.EmailField(unique=True, verbose_name='Електронна пошта')
    # Generation spends the shared Gemini key, so it is open only to confirmed addresses.
    email_verified = models.BooleanField(default=False, verbose_name='Пошту підтверджено')
    # For example 'Python Backend Developer'.
    target_position = models.CharField(max_length=50, blank=True, default='', verbose_name='Посада')
    experience_level = models.CharField(
        max_length=10, choices=LEVEL_CHOICES, blank=True, default='', verbose_name='Рівень',
    )

    USERNAME_FIELD = "email"
    # username is a generated technical value; email is the login.
    REQUIRED_FIELDS = ["username"]

    objects = CustomUserManager()

    class Meta:
        ordering = ['-date_joined']
        verbose_name = 'Користувач'
        verbose_name_plural = 'Користувачі'

    def __str__(self):
        return self.email
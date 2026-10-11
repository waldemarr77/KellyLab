from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    LEVEL_CHOICES = [
        ('trainee', 'Trainee'),
        ('junior', 'Junior'),
        ('middle', 'Middle'),
        ('senior', 'Senior'),
    ]

    email = models.EmailField(unique=True, verbose_name='Електронна пошта')
    # For example 'Python Backend Developer'.
    target_position = models.CharField(max_length=50, blank=True, default='', verbose_name='Посада')
    experience_level = models.CharField(
        max_length=10, choices=LEVEL_CHOICES, blank=True, default='', verbose_name='Рівень',
    )

    USERNAME_FIELD = "email"
    # username is a generated technical value; email is the login.
    REQUIRED_FIELDS = ["username"]

    class Meta:
        ordering = ['-date_joined']
        verbose_name = 'Користувач'
        verbose_name_plural = 'Користувачі'

    def __str__(self):
        return self.email
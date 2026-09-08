from django.db import models
from django.contrib.auth.models import AbstractUser

class CustomUser(AbstractUser):
    LEVEL_CHOICES = [
        ('trainee', 'Trainee'),
        ('junior', 'Junior'),
        ('middle', 'Middle'),
        ('senior', 'Senior'),
    ]

    email = models.EmailField(unique=True, verbose_name='Електронна пошта')
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True, verbose_name='Аватарка')
    target_position = models.CharField(max_length=50, blank=True, null=True, verbose_name='Посада') # for example 'Python Backend Developer'
    experience_level = models.CharField(max_length=10, choices=LEVEL_CHOICES, blank=True, null=True, verbose_name='Рівень')

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]


    class Meta:
        ordering = ['-username',]
        verbose_name = 'Користувач'
        verbose_name_plural = 'Користувачі'

    def __str__(self):
        return f'{self.username} {self.email}'
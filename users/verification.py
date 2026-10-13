import logging

from django.conf import settings
from django.contrib.auth.tokens import (
    PasswordResetTokenGenerator,
    default_token_generator,
)
from django.core.mail import send_mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

logger = logging.getLogger(__name__)


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = 'users.verification.EmailVerificationTokenGenerator'

    def _make_hash_value(self, user, timestamp):
        # The token stops working once the address is confirmed or changed.
        return f'{user.pk}{user.email}{user.email_verified}{timestamp}'


email_verification_token = EmailVerificationTokenGenerator()


def send(user, subject, message):
    # A mail failure must not break the request: the user can ask for the letter again.
    try:
        send_mail(subject, message, None, [user.email])
    except Exception:
        logger.exception('Could not send email to user %s', user.pk)


def make_link(template, user, token_generator):
    return template.format(uid=urlsafe_base64_encode(force_bytes(user.pk)), token=token_generator.make_token(user))


def send_verification_email(user):
    link = make_link(settings.EMAIL_VERIFICATION_URL, user, email_verification_token)
    send(user, 'KellyLab: підтвердження пошти', (
        f'Щоб підтвердити пошту і відкрити генерацію відповідей, перейди за посиланням:\n{link}\n\n'
        'Якщо ти не реєструвався в KellyLab, просто проігноруй цей лист.'
    ))


def send_already_registered_email(user):
    send(user, 'KellyLab: спроба повторної реєстрації', (
        'Хтось намагався зареєструвати акаунт KellyLab на цю пошту, але він уже існує. '
        'Якщо це був ти, просто увійди. Якщо ні, нічого робити не потрібно.'
    ))


def send_password_reset_email(user):
    # default_token_generator hashes the password and last login, so the link works once.
    link = make_link(settings.PASSWORD_RESET_URL, user, default_token_generator)
    send(user, 'KellyLab: відновлення пароля', (
        f'Щоб задати новий пароль, перейди за посиланням:\n{link}\n\n'
        'Якщо ти не просив відновлення, просто проігноруй цей лист: пароль залишиться тим самим.'
    ))

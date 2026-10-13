from uuid import uuid4

from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework import serializers
from rest_framework.exceptions import Throttled
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .backends import login_locked
from .models import CustomUser
from .verification import (
    email_verification_token,
    send_already_registered_email,
    send_verification_email,
)


def password_field():
    return serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)


def check_new_password(password, user, field='new_password'):
    try:
        validate_password(password, user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({field: exc.messages}) from exc


def user_from_link(uid, token, token_generator):
    """The user a uid/token link from a letter belongs to, if the token is still valid."""
    try:
        user = CustomUser.objects.get(pk=force_str(urlsafe_base64_decode(uid)))
    except (ValueError, OverflowError, CustomUser.DoesNotExist):
        user = None
    if user is None or not token_generator.check_token(user, token):
        raise serializers.ValidationError('Посилання недійсне або застаріле. Попроси новий лист.')
    return user


class EmailTokenSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        attrs['email'] = attrs['email'].strip().lower()
        if login_locked(self.context['request'], attrs['email']):
            raise Throttled(detail='Забагато невдалих спроб входу. Спробуй пізніше.')
        return super().validate(attrs)


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ('id', 'email', 'email_verified', 'target_position', 'experience_level')
        read_only_fields = ('id', 'email', 'email_verified')


class RegisterSerializer(serializers.ModelSerializer):
    password = password_field()

    class Meta:
        model = CustomUser
        fields = ('id', 'email', 'password', 'target_position', 'experience_level')
        read_only_fields = ('id',)
        # The unique validator would reveal that the email is taken; create() handles it instead.
        extra_kwargs = {'email': {'validators': []}}

    def validate_email(self, value):
        return value.strip().lower()

    def validate(self, attrs):
        check_new_password(attrs['password'], CustomUser(email=attrs['email']), field='password')
        return attrs

    def create(self, validated_data):
        """Answers the same way whether or not the email is taken; the owner of the address gets a letter."""
        existing = CustomUser.objects.filter(email__iexact=validated_data['email']).first()
        if existing is None:
            try:
                with transaction.atomic():
                    user = CustomUser.objects.create_user(username=uuid4().hex, **validated_data)
            except IntegrityError:
                # A parallel request registered the same email first.
                existing = CustomUser.objects.get(email__iexact=validated_data['email'])
            else:
                send_verification_email(user)
                return user
        # Hash the password anyway so the response time does not reveal an existing account.
        CustomUser().set_password(validated_data['password'])
        send_already_registered_email(existing)
        return existing


class VerifyEmailSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()

    def validate(self, attrs):
        return {'user': user_from_link(attrs['uid'], attrs['token'], email_verification_token)}


class CurrentPasswordSerializer(serializers.Serializer):
    """Confirms a sensitive action with the user's current password."""

    password = password_field()

    def validate_password(self, value):
        if not self.context['request'].user.check_password(value):
            raise serializers.ValidationError('Неправильний пароль.')
        return value


class PasswordChangeSerializer(CurrentPasswordSerializer):
    new_password = password_field()

    def validate(self, attrs):
        check_new_password(attrs['new_password'], self.context['request'].user)
        return attrs


class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = password_field()

    def validate(self, attrs):
        user = user_from_link(attrs['uid'], attrs['token'], default_token_generator)
        check_new_password(attrs['new_password'], user)
        return {'user': user, 'new_password': attrs['new_password']}

from uuid import uuid4

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import CustomUser


class EmailTokenSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        attrs['email'] = attrs['email'].strip().lower()
        return super().validate(attrs)


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ('id', 'email', 'target_position', 'experience_level')
        read_only_fields = ('id', 'email')


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)

    class Meta:
        model = CustomUser
        fields = ('id', 'email', 'password', 'target_position', 'experience_level')
        read_only_fields = ('id',)

    def validate_email(self, value):
        value = value.strip().lower()
        if CustomUser.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('Користувач із такою поштою вже існує.')
        return value

    def validate(self, attrs):
        try:
            validate_password(attrs['password'], CustomUser(email=attrs['email']))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': exc.messages}) from exc
        return attrs

    def create(self, validated_data):
        return CustomUser.objects.create_user(username=uuid4().hex, **validated_data)

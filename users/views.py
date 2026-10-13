from drf_yasg import openapi
from drf_yasg.utils import no_body, swagger_auto_schema
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.views import (
    TokenBlacklistView,
    TokenObtainPairView,
    TokenRefreshView,
)

from .models import CustomUser
from .serializers import (
    CurrentPasswordSerializer,
    EmailTokenSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetSerializer,
    ProfileSerializer,
    RegisterSerializer,
    VerifyEmailSerializer,
)
from .verification import send_password_reset_email, send_verification_email

DETAIL_RESPONSE = openapi.Response('detail', openapi.Schema(
    type=openapi.TYPE_OBJECT, properties={'detail': openapi.Schema(type=openapi.TYPE_STRING)},
))


def revoke_refresh_tokens(user):
    """Log the user out everywhere: every refresh token stops working. Access tokens expire within 15 min."""
    tokens = OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True)
    BlacklistedToken.objects.bulk_create([BlacklistedToken(token=token) for token in tokens], ignore_conflicts=True)


def set_new_password(user, password, **fields):
    user.set_password(password)
    for name, value in fields.items():
        setattr(user, name, value)
    user.save(update_fields=['password', *fields])
    revoke_refresh_tokens(user)


class AuthThrottleMixin:
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = 'auth'


# Separate from 'auth' so token refreshes do not use up login attempts from the same IP.
class TokenThrottleMixin:
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = 'token'


class EmailTokenView(AuthThrottleMixin, TokenObtainPairView):
    serializer_class = EmailTokenSerializer


class RegisterView(AuthThrottleMixin, generics.CreateAPIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = RegisterSerializer

    @swagger_auto_schema(responses={202: DETAIL_RESPONSE})
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        # The same answer for a new and an existing email, so the endpoint does not reveal accounts.
        return Response(
            {'detail': 'Ми надіслали лист на вказану пошту. Підтверди її, щоб генерувати відповіді.'},
            status=status.HTTP_202_ACCEPTED,
        )


class VerifyEmailView(AuthThrottleMixin, APIView):
    permission_classes = (permissions.AllowAny,)
    # Also reachable from a logged-out browser tab, so no session auth (and no CSRF) is needed.
    authentication_classes = ()

    @swagger_auto_schema(request_body=VerifyEmailSerializer, responses={200: DETAIL_RESPONSE})
    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        user.email_verified = True
        user.save(update_fields=['email_verified'])
        return Response({'detail': 'Пошту підтверджено.'})


class ResendVerificationView(APIView):
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = 'verification_email'

    @swagger_auto_schema(request_body=no_body, responses={202: DETAIL_RESPONSE})
    def post(self, request):
        if request.user.email_verified:
            raise ValidationError('Пошту вже підтверджено.')
        send_verification_email(request.user)
        return Response({'detail': 'Лист надіслано ще раз.'}, status=status.HTTP_202_ACCEPTED)


class PasswordChangeView(AuthThrottleMixin, APIView):
    @swagger_auto_schema(request_body=PasswordChangeSerializer, responses={200: DETAIL_RESPONSE})
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        set_new_password(request.user, serializer.validated_data['new_password'])
        return Response({'detail': 'Пароль змінено. Увійди знову з новим паролем.'})


class PasswordResetView(AuthThrottleMixin, APIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    @swagger_auto_schema(request_body=PasswordResetSerializer, responses={202: DETAIL_RESPONSE})
    def post(self, request):
        serializer = PasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email'].strip()
        user = CustomUser.objects.filter(email__iexact=email, is_active=True).first()
        if user is not None:
            send_password_reset_email(user)
        # The same answer whether or not the account exists.
        return Response(
            {'detail': 'Якщо акаунт із такою поштою існує, ми надіслали на неї лист для відновлення пароля.'},
            status=status.HTTP_202_ACCEPTED,
        )


class PasswordResetConfirmView(AuthThrottleMixin, APIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    @swagger_auto_schema(request_body=PasswordResetConfirmSerializer, responses={200: DETAIL_RESPONSE})
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # The link came to this mailbox, so the address is confirmed as well.
        set_new_password(
            serializer.validated_data['user'], serializer.validated_data['new_password'], email_verified=True,
        )
        return Response({'detail': 'Пароль змінено. Тепер можна увійти.'})


class ThrottledTokenRefreshView(TokenThrottleMixin, TokenRefreshView):
    pass


class LogoutView(TokenThrottleMixin, TokenBlacklistView):
    pass


class ProfileView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProfileSerializer
    http_method_names = ['get', 'patch', 'delete', 'head', 'options']
    throttle_scope = 'auth'

    def get_object(self):
        return self.request.user

    def get_throttles(self):
        # Deleting checks the password, so it shares the limit with other password attempts.
        if self.request.method == 'DELETE':
            return [ScopedRateThrottle()]
        return super().get_throttles()

    @swagger_auto_schema(
        operation_description='Видалити акаунт з усіма сесіями, питаннями й експортами. Тіло: {"password": "..."}.',
        responses={204: 'Акаунт видалено'},
    )
    def delete(self, request, *args, **kwargs):
        serializer = CurrentPasswordSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        revoke_refresh_tokens(request.user)
        # Sessions, questions, exports (with their files) and usage counters go by cascade.
        request.user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

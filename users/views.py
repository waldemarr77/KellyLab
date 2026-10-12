from rest_framework import generics, permissions
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import (
    TokenBlacklistView,
    TokenObtainPairView,
    TokenRefreshView,
)

from .serializers import EmailTokenSerializer, ProfileSerializer, RegisterSerializer


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


class ThrottledTokenRefreshView(TokenThrottleMixin, TokenRefreshView):
    pass


class LogoutView(TokenThrottleMixin, TokenBlacklistView):
    pass


class ProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = ProfileSerializer
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_object(self):
        return self.request.user

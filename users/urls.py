from django.urls import path

from .views import (
    EmailTokenView,
    LogoutView,
    ProfileView,
    RegisterView,
    ThrottledTokenRefreshView,
)

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('token/', EmailTokenView.as_view(), name='token'),
    path('token/refresh/', ThrottledTokenRefreshView.as_view(), name='token-refresh'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('me/', ProfileView.as_view(), name='profile'),
]

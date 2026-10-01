from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import EmailTokenView, ProfileView, RegisterView

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('token/', EmailTokenView.as_view(), name='token'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('me/', ProfileView.as_view(), name='profile'),
]

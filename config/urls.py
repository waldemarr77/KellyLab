"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework.permissions import AllowAny
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from questions.views import QuestionViewSet
from sessions.views import SessionViewSet
from users.views import EmailTokenView, ProfileView, RegisterView

router = DefaultRouter()
router.register('sessions', SessionViewSet, basename='session')
router.register('questions', QuestionViewSet, basename='question')
schema_view = get_schema_view(
    openapi.Info(title='KellyLab API', default_version='v1', description='Підготовка до співбесід'),
    public=True, permission_classes=(AllowAny,), authentication_classes=(),
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/register/', RegisterView.as_view(), name='register'),
    path('api/auth/token/', EmailTokenView.as_view(), name='token'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('api/auth/me/', ProfileView.as_view(), name='profile'),
    path('api/', include(router.urls)),
    path('api/docs/', schema_view.with_ui('swagger', cache_timeout=0), name='api-docs'),
    path('api/schema/', schema_view.without_ui(cache_timeout=0), name='api-schema'),
]

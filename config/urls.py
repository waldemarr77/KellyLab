from django.contrib import admin
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework.permissions import AllowAny
from rest_framework.routers import DefaultRouter

from interviews.views import SessionViewSet
from questions.views import QuestionViewSet

router = DefaultRouter()
router.register('sessions', SessionViewSet, basename='session')
router.register('questions', QuestionViewSet, basename='question')
schema_view = get_schema_view(
    openapi.Info(title='KellyLab API', default_version='v1', description='Підготовка до співбесід'),
    public=True, permission_classes=(AllowAny,), authentication_classes=(),
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('users.urls')),
    path('api/', include(router.urls)),
    path('api/docs/', schema_view.with_ui('swagger', cache_timeout=0), name='api-docs'),
    path('api/schema/', schema_view.without_ui(cache_timeout=0), name='api-schema'),
]

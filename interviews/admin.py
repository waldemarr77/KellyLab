from django.contrib import admin

from questions.admin import QuestionInline

from .models import AIUsage, Session


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'user',
        'name',
        'status',
        'celery_task_id',
        'created_at',
        'updated_at',
    ]
    list_filter = ['status']
    search_fields = [
        'user__email',
        'name',
        'celery_task_id',
    ]
    list_select_related = ['user']
    readonly_fields = [
        'id',
        'celery_task_id',
        'created_at',
        'updated_at',
    ]
    ordering = ['-created_at', '-pk']
    date_hierarchy = 'created_at'
    inlines = [QuestionInline]

    fieldsets = (
        ('Основне', {
            'fields': ('user', 'name', 'status'),
        }),
        ('Технічні дані', {
            'fields': ('id', 'celery_task_id'),
            'classes': ('collapse',),
        }),
        ('Дати', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(AIUsage)
class AIUsageAdmin(admin.ModelAdmin):
    list_display = ['user', 'date', 'questions']
    search_fields = ['user__email']
    list_select_related = ['user']
    date_hierarchy = 'date'

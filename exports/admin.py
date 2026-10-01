from django.contrib import admin

from .models import Export


@admin.register(Export)
class ExportAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'session',
        'format',
        'status',
        'file',
        'created_at',
        'completed_at',
    ]

    list_filter = [
        'format',
        'status',
    ]

    search_fields = [
        'session__name',
        'session__user__username',
        'session__user__email',
        'celery_task_id',
        'content_hash',
    ]
    list_select_related = ['session__user']
    readonly_fields = [
        'created_at',
        'updated_at',
        'completed_at',
    ]

    ordering = ['-created_at', '-pk']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Основне', {
            'fields': ('session', 'format', 'status'),
        }),
        ('Результат експорту', {
            'fields': ('file', 'error_message'),
        }),
        ('Технічні дані', {
            'fields': ('celery_task_id', 'content_hash'),
            'classes': ('collapse',),
        }),
        ('Дати', {
            'fields': ('created_at', 'updated_at', 'completed_at'),
        }),
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'session':
            kwargs['queryset'] = db_field.remote_field.model.objects.select_related('user')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

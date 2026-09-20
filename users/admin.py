from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = [
        'email',
        'username',
        'target_position',
        'experience_level',
        'is_active',
        'is_staff',
        'is_superuser',
        'date_joined',
        'last_login',
    ]

    list_filter = [
        'experience_level',
        'is_active',
        'is_staff',
        'is_superuser',
        'groups',
    ]

    search_fields = [
        'username',
        'email',
        'target_position',
    ]

    readonly_fields = ['date_joined', 'last_login']
    ordering = ['username']
    date_hierarchy = 'date_joined'

    fieldsets = UserAdmin.fieldsets + (
        ('Підготовка до співбесіди', {
            'fields': ('target_position', 'experience_level'),
        }),
        ('Аватар', {
            'fields': ('avatar',),
        }),
    )

    add_fieldsets = tuple(
        (title, {**options, 'fields': ('email', *options['fields'])})
        for title, options in UserAdmin.add_fieldsets
    ) + (
        ('Додаткова інформація', {
            'fields': (
                'target_position', 'experience_level', 'avatar',
            ),
        }),
    )

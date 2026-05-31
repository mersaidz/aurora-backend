from django.contrib import admin
from .models import (
    SportType, Workout, UserPhysioProfile, DataSource,
    HealthMetrics, LactateMeasurement, AuditLog,
)


@admin.register(Workout)
class WorkoutAdmin(admin.ModelAdmin):
    list_display = ('user', 'sport_type', 'date', 'duration', 'rpe', 'avg_hr', 'training_load')
    list_filter = ('sport_type', 'verification_level', 'is_primary', 'rpe')
    search_fields = ('user__email', 'user__username')
    date_hierarchy = 'date'
    raw_id_fields = ('user', 'sport_type', 'source', 'user_physio_profile', 'duplicate_of')
    readonly_fields = ('created_at', 'updated_at', 'training_load_calculated_at')


@admin.register(SportType)
class SportTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'parent')
    list_filter = ('category',)
    search_fields = ('name',)
    raw_id_fields = ('parent',)


@admin.register(UserPhysioProfile)
class UserPhysioProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'method', 'max_hr', 'rest_hr', 'threshold_hr', 'is_active', 'created_at')
    list_filter = ('method', 'is_active')
    search_fields = ('user__email', 'user__username')
    raw_id_fields = ('user',)
    readonly_fields = ('created_at',)


@admin.register(DataSource)
class DataSourceAdmin(admin.ModelAdmin):
    list_display = ('user', 'platform', 'is_active', 'connected_at', 'token_expires')
    list_filter = ('platform', 'is_active')
    search_fields = ('user__email', 'user__username', 'provider_user_id')
    raw_id_fields = ('user',)
    # Never render encrypted token fields in admin — even decrypted, they shouldn't be on screen.
    exclude = ('access_token', 'refresh_token')
    readonly_fields = ('connected_at',)


@admin.register(HealthMetrics)
class HealthMetricsAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'source', 'recovery_score', 'sleep_score', 'hrv', 'is_primary')
    list_filter = ('is_primary', 'source__platform')
    search_fields = ('user__email', 'user__username')
    date_hierarchy = 'date'
    raw_id_fields = ('user', 'source')


@admin.register(LactateMeasurement)
class LactateMeasurementAdmin(admin.ModelAdmin):
    list_display = ('workout', 'measured_at', 'mmol', 'hr_bpm')
    raw_id_fields = ('workout',)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('action', 'user', 'platform', 'ip_address', 'created_at')
    list_filter = ('action', 'platform')
    search_fields = ('user__email', 'user__username', 'ip_address')
    date_hierarchy = 'created_at'
    raw_id_fields = ('user',)
    # AuditLog is append-only — block edits from the admin UI too.
    readonly_fields = ('user', 'action', 'platform', 'ip_address', 'created_at', 'extra_info')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

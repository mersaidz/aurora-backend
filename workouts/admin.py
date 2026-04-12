from django.contrib import admin
from .models import SportType, Workout

@admin.register(Workout)
class WorkoutAdmin(admin.ModelAdmin):
    list_display = ('user', 'sport_type', 'date', 'duration', 'rpe', 'avg_hr', 'training_load')
    list_filter = ('sport_type', 'rpe', 'date')
    search_fields = ('user__email', 'notes')

@admin.register(SportType)
class SportTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'category')
    list_filter = ('category',)
    search_fields = ('name',)


# Register your models here.

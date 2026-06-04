from django.contrib import admin

from survey_datasource.models import SurveyDataSource

# Register your models here.
@admin.register(SurveyDataSource)
class SurveyDataSourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'device_id', 'profile')
    search_fields = ['name', 'device_id', 'profile__user__username']


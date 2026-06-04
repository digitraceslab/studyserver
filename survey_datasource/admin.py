from django.contrib import admin
from polymorphic.admin import PolymorphicChildModelAdmin

from data_sources.models import DataSource
from survey_datasource.models import SurveyDataSource

# Register your models here.
@admin.register(SurveyDataSource)
class SurveyDataSourceAdmin(PolymorphicChildModelAdmin):
    base_model = DataSource
    show_in_index = True
    readonly_fields = ('device_id',)
    list_display = ('name', 'device_id', 'profile')
    search_fields = ['name', 'device_id', 'profile__user__username']


DataSource.register_admin_child_model(SurveyDataSource)


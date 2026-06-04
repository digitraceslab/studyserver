from django.contrib import admin
from polymorphic.admin import PolymorphicChildModelAdmin

from data_sources.models import DataSource
from data_sources.models.model_registration import datasourceadmin
from survey_datasource.models import SurveyDataSource


@datasourceadmin(SurveyDataSource)
@admin.register(SurveyDataSource)
class SurveyDataSourceAdmin(PolymorphicChildModelAdmin):
    base_model = DataSource
    show_in_index = True
    readonly_fields = ('device_id',)
    list_display = ('name', 'device_id', 'profile')
    search_fields = ['name', 'device_id', 'profile__user__username']



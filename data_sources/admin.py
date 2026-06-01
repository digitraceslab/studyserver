from django.contrib import admin
from polymorphic.admin import PolymorphicParentModelAdmin, PolymorphicChildModelAdmin
from .models import DataSource, NiimportDataSource, AwareDataSource, JsonUrlDataSource

COMMON_READ_ONLY_FIELDS = ('device_id',)

@admin.register(JsonUrlDataSource)
class JsonUrlDataSourceAdmin(PolymorphicChildModelAdmin):
    base_model = DataSource
    show_in_index = True
    readonly_fields = COMMON_READ_ONLY_FIELDS

@admin.register(AwareDataSource)
class AwareDataSourceAdmin(PolymorphicChildModelAdmin):
    base_model = DataSource
    show_in_index = True
    readonly_fields = COMMON_READ_ONLY_FIELDS
    search_fields = ['device_id', 'device_label', 'name', 'profile__user__username']
    list_display = ('name', 'device_id', 'device_label', 'status', 'profile')

@admin.register(NiimportDataSource)
class NiimportDataSourceAdmin(PolymorphicChildModelAdmin):
    base_model = DataSource
    show_in_index = True
    readonly_fields = COMMON_READ_ONLY_FIELDS + ('donation_id', 'donation_token',)
    

@admin.register(DataSource)
class DataSourceAdmin(PolymorphicParentModelAdmin):
    base_model = DataSource
    child_models = (JsonUrlDataSource, AwareDataSource, NiimportDataSource)



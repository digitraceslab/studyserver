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


# Explicit child registrations for the polymorphic parent admin.
DataSource.register_admin_child_model(JsonUrlDataSource)
DataSource.register_admin_child_model(AwareDataSource)
DataSource.register_admin_child_model(NiimportDataSource)
    

@admin.register(DataSource)
class DataSourceAdmin(PolymorphicParentModelAdmin):
    base_model = DataSource

    def get_child_models(self):
        return DataSource.get_registered_admin_child_models()



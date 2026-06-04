from django.contrib import admin
from polymorphic.admin import PolymorphicParentModelAdmin, PolymorphicChildModelAdmin
from .models import DataSource, NiimportDataSource, AwareDataSource, JsonUrlDataSource
from .models.model_registration import datasourceadmin

COMMON_READ_ONLY_FIELDS = ('device_id',)

@datasourceadmin(JsonUrlDataSource)
@admin.register(JsonUrlDataSource)
class JsonUrlDataSourceAdmin(PolymorphicChildModelAdmin):
    base_model = DataSource
    show_in_index = True
    readonly_fields = COMMON_READ_ONLY_FIELDS

@datasourceadmin(AwareDataSource)
@admin.register(AwareDataSource)
class AwareDataSourceAdmin(PolymorphicChildModelAdmin):
    base_model = DataSource
    show_in_index = True
    readonly_fields = COMMON_READ_ONLY_FIELDS
    search_fields = ['device_id', 'device_label', 'name', 'profile__user__username']
    list_display = ('name', 'device_id', 'device_label', 'status', 'profile')

@datasourceadmin(NiimportDataSource)
@admin.register(NiimportDataSource)
class NiimportDataSourceAdmin(PolymorphicChildModelAdmin):
    base_model = DataSource
    show_in_index = True
    readonly_fields = COMMON_READ_ONLY_FIELDS + ('donation_id', 'donation_token',)


@admin.register(DataSource)
class DataSourceAdmin(PolymorphicParentModelAdmin):
    base_model = DataSource

    def get_child_models(self):
        return DataSource.get_registered_admin_child_models()



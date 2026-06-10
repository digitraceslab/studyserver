from django.contrib import admin
from .models import Profile, ProtectedIdentifier

admin.site.register(Profile)


@admin.register(ProtectedIdentifier)
class ProtectedIdentifierAdmin(admin.ModelAdmin):
    list_display = ('profile', 'value')

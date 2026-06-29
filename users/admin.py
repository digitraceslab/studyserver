from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from studies.models import StudyParticipant
from .models import Profile, ProtectedIdentifier


class StudyParticipantInline(admin.TabularInline):
    """Study participations for a profile, shown on the user/Profile page so
    admins can look a participant up by email and find their participant id.
    Researchers never see the user admin, so this exposes no identities to them."""
    model = StudyParticipant
    fk_name = 'participant'
    fields = ('study', 'pseudo_id', 'change_link')
    readonly_fields = ('study', 'pseudo_id', 'change_link')
    can_delete = False
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description='')
    def change_link(self, obj):
        if not obj.pk:
            return '-'
        url = reverse('admin:studies_studyparticipant_change', args=[obj.pk])
        return format_html('<a href="{}">View consents</a>', url)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'user_type', 'email')
    list_filter = ('user_type',)
    search_fields = ('user__username', 'user__email')
    inlines = [StudyParticipantInline]

    @admin.display(description='Email')
    def email(self, obj):
        return obj.user.email


@admin.register(ProtectedIdentifier)
class ProtectedIdentifierAdmin(admin.ModelAdmin):
    list_display = ('profile', 'value')

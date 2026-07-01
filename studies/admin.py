import json
import re
import uuid
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMessage
from django.db import models
from django import forms
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.html import format_html
from django.urls import path, reverse
from django_ace import AceWidget
from .models import Study, Consent, StudyParticipant, StudySourceConfiguration, StudyAsset, PrivacyNotice, TermsOfService
from .forms import StudyAdminForm, SourceConfigurationInlineForm, StudySourceConfigurationForm
from data_sources.models import DataSource


class PrettyJSONFormField(forms.JSONField):
    widget = AceWidget(mode='json', theme='monokai', width='100%', height='300px')

    def prepare_value(self, value):
        if isinstance(value, (dict, list)):
            return json.dumps(value, indent=2)
        return super().prepare_value(value)

class SendParticipantEmailForm(forms.Form):
    subject = forms.CharField(max_length=255)
    message = forms.CharField(widget=forms.Textarea)
    reply_to = forms.ChoiceField(
        label='Reply-to',
        help_text='Where participant replies should be sent.',
    )

    def __init__(self, *args, study_contacts=None, user_email=None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = []
        for contact in study_contacts or []:
            choices.append((contact, f'Study contact ({contact})'))
        if user_email:
            choices.append((user_email, f'My email ({user_email})'))
        if choices:
            self.fields['reply_to'].choices = choices
        else:
            # No address to reply to; hide the field rather than block sending.
            del self.fields['reply_to']


class SendParticipantEmailByIdsForm(SendParticipantEmailForm):
    """Email form that also takes a pasted list of participant pseudo-IDs, for
    targeting a cohort computed off-server. Invalid tokens are collected in
    ``invalid_pseudo_ids`` so the view can report them."""
    pseudo_ids = forms.CharField(
        widget=forms.Textarea,
        label='Participant pseudo-IDs',
        help_text='One pseudo-ID per line (spaces or commas also accepted).',
    )
    field_order = ['pseudo_ids', 'subject', 'message', 'reply_to']

    def clean_pseudo_ids(self):
        self.invalid_pseudo_ids = []
        valid = []
        for token in re.split(r'[\s,]+', self.cleaned_data['pseudo_ids']):
            if not token:
                continue
            try:
                valid.append(uuid.UUID(token))
            except ValueError:
                self.invalid_pseudo_ids.append(token)
        if not valid:
            raise forms.ValidationError('No valid pseudo-IDs found.')
        return valid


def _consent_summary(study_participant):
    """(required_complete, required_total, optional_complete, optional_total) for a participant."""
    consents = study_participant.consents.filter(revocation_date__isnull=True)
    required = consents.filter(is_optional=False)
    optional = consents.filter(is_optional=True)
    return (
        required.filter(is_complete=True, data_source__status='active').count(),
        required.count(),
        optional.filter(is_complete=True).count(),
        optional.count(),
    )


class ConsentInline(admin.TabularInline):
    """Consents for a single participant, shown as rows on the StudyParticipant page.

    Deliberately omits participant username/email — participants are identified
    only by their pseudonymous id.
    """
    model = Consent
    fields = ('source_type', 'data_source_info', 'is_optional', 'is_complete', 'consent_date')
    readonly_fields = (
        'source_type',
        'data_source_info',
        'is_optional',
        'is_complete',
        'consent_date',
    )
    can_delete = False
    extra = 0

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        if request.user.is_superuser and 'change_link' not in fields:
            return tuple(fields) + ('change_link',)
        return fields

    def get_readonly_fields(self, request, obj=None):
        readonly = super().get_readonly_fields(request, obj)
        if request.user.is_superuser and 'change_link' not in readonly:
            return tuple(readonly) + ('change_link',)
        return readonly

    @admin.display(description='')
    def change_link(self, obj):
        if not obj.pk:
            return '-'
        url = reverse('admin:studies_consent_change', args=[obj.pk])
        return format_html('<a href="{}">Edit</a>', url)

    @admin.display(description='Data Source')
    def data_source_info(self, obj):
        if not obj.data_source:
            return "Not linked"
        source = obj.data_source.get_real_instance()
        status_color = 'green' if source.status == 'active' else 'orange'
        return format_html(
            '<span style="color: {};">{}</span> ({})',
            status_color,
            source.name,
            source.status,
        )

    def has_add_permission(self, request, obj=None):
        return False


class StudyParticipantInline(admin.TabularInline):
    """Participants of a study, shown as rows on the Study page with a
    completed/total consent summary instead of the individual consents."""
    model = StudyParticipant
    fields = ('pseudo_id', 'researcher_approved', 'consents_summary', 'status', 'change_link')
    readonly_fields = ('pseudo_id', 'consents_summary', 'status', 'change_link')
    can_delete = False
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description='Consents (complete / total)')
    def consents_summary(self, obj):
        rc, rt, oc, ot = _consent_summary(obj)
        return f"required {rc}/{rt}, optional {oc}/{ot}"

    @admin.display(description='Status')
    def status(self, obj):
        rc, rt, _, _ = _consent_summary(obj)
        return 'Complete' if rt and rc == rt else 'Incomplete'

    @admin.display(description='')
    def change_link(self, obj):
        if not obj.pk:
            return '-'
        url = reverse('admin:studies_studyparticipant_change', args=[obj.pk])
        return format_html('<a href="{}">View consents</a>', url)


@admin.register(StudyParticipant)
class StudyParticipantAdmin(admin.ModelAdmin):
    list_display = ('study', 'pseudo_id', 'researcher_approved', 'consents_summary')
    list_editable = ('researcher_approved',)
    fields = ('study', 'pseudo_id', 'researcher_approved')
    readonly_fields = ('study', 'pseudo_id')
    inlines = [ConsentInline]
    change_form_template = 'admin/studies/studyparticipant/change_form.html'
    change_list_template = 'admin/studies/studyparticipant/change_list.html'
    actions = ['approve_participants', 'unapprove_participants', 'send_bulk_email']

    @admin.action(description='Approve selected participants')
    def approve_participants(self, request, queryset):
        updated = queryset.update(researcher_approved=True)
        self.message_user(
            request, f"Approved {updated} participant(s).", messages.SUCCESS
        )

    @admin.action(description='Revoke approval of selected participants')
    def unapprove_participants(self, request, queryset):
        updated = queryset.update(researcher_approved=False)
        self.message_user(
            request, f"Revoked approval of {updated} participant(s).", messages.SUCCESS
        )

    def set_approval_view(self, request, object_id, approved):
        # Per-object approve / revoke button on the change page. Scoped to
        # get_queryset so a researcher can only act on their own participants.
        participant = get_object_or_404(self.get_queryset(request), pk=object_id)
        if not self.has_change_permission(request, participant):
            raise PermissionDenied
        change_url = reverse(
            'admin:studies_studyparticipant_change', args=[participant.pk]
        )
        if request.method != 'POST':
            return redirect(change_url)
        participant.researcher_approved = approved
        participant.save(update_fields=['researcher_approved'])
        self.message_user(
            request,
            "Participant approved." if approved else "Participant approval revoked.",
            messages.SUCCESS,
        )
        return redirect(change_url)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                'send-email-by-ids/',
                self.admin_site.admin_view(self.send_email_by_ids_view),
                name='studies_studyparticipant_send_email_by_ids',
            ),
            path(
                '<path:object_id>/approve/',
                self.admin_site.admin_view(
                    lambda request, object_id: self.set_approval_view(
                        request, object_id, True
                    )
                ),
                name='studies_studyparticipant_approve',
            ),
            path(
                '<path:object_id>/unapprove/',
                self.admin_site.admin_view(
                    lambda request, object_id: self.set_approval_view(
                        request, object_id, False
                    )
                ),
                name='studies_studyparticipant_unapprove',
            ),
            path(
                '<path:object_id>/send-email/',
                self.admin_site.admin_view(self.send_email_view),
                name='studies_studyparticipant_send_email',
            ),
        ]
        return custom + urls

    def send_email_view(self, request, object_id):
        # Scope to get_queryset so a researcher can only email participants of
        # their own studies; raises 404 otherwise.
        participant = get_object_or_404(self.get_queryset(request), pk=object_id)
        if not self.has_view_permission(request, participant):
            raise PermissionDenied
        change_url = reverse(
            'admin:studies_studyparticipant_change', args=[participant.pk]
        )
        form_kwargs = {
            'study_contacts': [participant.study.contact_email]
            if participant.study.contact_email else [],
            'user_email': request.user.email,
        }
        if request.method == 'POST':
            form = SendParticipantEmailForm(request.POST, **form_kwargs)
            if form.is_valid():
                try:
                    sent = self._send_email_to_participant(
                        participant,
                        form.cleaned_data['subject'],
                        form.cleaned_data['message'],
                        form.cleaned_data.get('reply_to'),
                    )
                except Exception as exc:
                    self.message_user(
                        request, f"Failed to send email: {exc}", messages.ERROR
                    )
                    return redirect(change_url)
                if sent:
                    self.message_user(
                        request, "Email sent to the participant.", messages.SUCCESS
                    )
                else:
                    self.message_user(
                        request,
                        "Participant has no email address; nothing was sent.",
                        messages.WARNING,
                    )
                return redirect(change_url)
        else:
            form = SendParticipantEmailForm(**form_kwargs)
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'original': participant,
            'change_url': change_url,
            'form': form,
            'title': 'Send email to participant',
        }
        return render(
            request, 'admin/studies/studyparticipant/send_email.html', context
        )

    @admin.action(description='Send an email to selected participants')
    def send_bulk_email(self, request, queryset):
        # Django action-confirmation pattern: the first click renders the email
        # form (carrying the selection as hidden inputs); the 'send' marker on
        # the posted form tells us it's the confirmed submission.
        participants = list(queryset)
        contacts = []
        for p in participants:
            if p.study.contact_email and p.study.contact_email not in contacts:
                contacts.append(p.study.contact_email)
        form_kwargs = {'study_contacts': contacts, 'user_email': request.user.email}
        if 'send' in request.POST:
            form = SendParticipantEmailForm(request.POST, **form_kwargs)
            if form.is_valid():
                subject = form.cleaned_data['subject']
                message = form.cleaned_data['message']
                reply_to = form.cleaned_data.get('reply_to')
                sent = 0
                for participant in participants:
                    try:
                        sent += self._send_email_to_participant(
                            participant, subject, message, reply_to
                        )
                    except Exception as exc:
                        self.message_user(
                            request,
                            f"Failed to send some emails: {exc}",
                            messages.ERROR,
                        )
                        return None
                self.message_user(
                    request,
                    f"Email sent to {sent} of {len(participants)} selected "
                    "participants (participants without an address are skipped).",
                    messages.SUCCESS,
                )
                return None
        else:
            form = SendParticipantEmailForm(**form_kwargs)
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'form': form,
            'title': 'Send email to selected participants',
            'participants': participants,
            'selected_pks': [p.pk for p in participants],
            'action_checkbox_name': ACTION_CHECKBOX_NAME,
        }
        return render(
            request, 'admin/studies/studyparticipant/send_email.html', context
        )

    def _reply_to_contacts(self, request):
        """Distinct study contact emails visible to this user, for Reply-to."""
        contacts = []
        qs = self.get_queryset(request)
        for contact in qs.values_list('study__contact_email', flat=True).distinct():
            if contact and contact not in contacts:
                contacts.append(contact)
        return contacts

    def send_email_by_ids_view(self, request):
        # Email a cohort identified by pseudo-IDs computed off-server. IDs are
        # matched against get_queryset, so a researcher can only reach
        # participants of their own studies.
        changelist_url = reverse('admin:studies_studyparticipant_changelist')
        form_kwargs = {
            'study_contacts': self._reply_to_contacts(request),
            'user_email': request.user.email,
        }
        if request.method == 'POST':
            form = SendParticipantEmailByIdsForm(request.POST, **form_kwargs)
            if form.is_valid():
                ids = form.cleaned_data['pseudo_ids']
                participants = list(
                    self.get_queryset(request).filter(pseudo_id__in=ids)
                )
                found = {p.pseudo_id for p in participants}
                not_found = [str(i) for i in ids if i not in found]
                subject = form.cleaned_data['subject']
                message = form.cleaned_data['message']
                reply_to = form.cleaned_data.get('reply_to')
                sent = 0
                for participant in participants:
                    try:
                        sent += self._send_email_to_participant(
                            participant, subject, message, reply_to
                        )
                    except Exception as exc:
                        self.message_user(
                            request, f"Failed to send some emails: {exc}",
                            messages.ERROR,
                        )
                        return redirect(changelist_url)
                self.message_user(
                    request,
                    f"Email sent to {sent} participant(s). "
                    f"{len(not_found)} pseudo-ID(s) matched no participant; "
                    f"{len(form.invalid_pseudo_ids)} were not valid IDs.",
                    messages.SUCCESS,
                )
                return redirect(changelist_url)
        else:
            form = SendParticipantEmailByIdsForm(**form_kwargs)
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'form': form,
            'title': 'Send email to participants by pseudo-ID',
            'by_ids': True,
            'change_url': changelist_url,
        }
        return render(
            request, 'admin/studies/studyparticipant/send_email.html', context
        )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(study__researchers=request.user.profile)

    def has_add_permission(self, request):
        return False

    @admin.display(description='Consents (complete / total)')
    def consents_summary(self, obj):
        rc, rt, oc, ot = _consent_summary(obj)
        return f"required {rc}/{rt}, optional {oc}/{ot}"

    def _send_email_to_participant(self, study_participant, subject, message,
                                   reply_to=None):
        """Send one email synchronously, never exposing the recipient address.

        The From address stays on the authenticated DEFAULT_FROM_EMAIL domain so
        it isn't spam-filtered; reply_to (the sender's chosen Reply-to address)
        is where participant replies go. Returns 1 on success, 0 if the
        participant has no usable email address.
        """
        if not study_participant.participant:
            return 0
        recipient = study_participant.participant.user.email
        if not recipient:
            return 0
        email = EmailMessage(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            reply_to=[reply_to] if reply_to else None,
        )
        return email.send(fail_silently=False)


@admin.register(Consent)
class ConsentAdmin(admin.ModelAdmin):
    list_display = (
        'study',
        'participant_pseudo_id',
        'source_type',
        'data_source_status',
        'is_complete',
        'consent_date'
    )
    # Fields shown to researchers — identifies the participant by pseudonymous id
    # only, never by username/email or the participant/study_participant FKs
    # (whose __str__ could expose identity).
    researcher_fields = (
        'study',
        'participant_pseudo_id',
        'source_type',
        'data_source_status',
        'is_optional',
        'is_complete',
        'consent_text_accepted',
        'consent_date',
        'revocation_date',
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs

        return qs.filter(study__researchers=request.user.profile)

    def get_fields(self, request, obj=None):
        if request.user.is_superuser:
            return super().get_fields(request, obj)
        return self.researcher_fields

    def get_readonly_fields(self, request, obj=None):
        if request.user.is_superuser:
            return ('participant', 'study', 'source_type', 'consent_date')
        return self.researcher_fields

    @admin.display(description='Participant ID')
    def participant_pseudo_id(self, obj):
        if obj.study_participant:
            return obj.study_participant.pseudo_id
        return "-"

    @admin.display(description='Source Status')
    def data_source_status(self, obj):
        if not obj.data_source:
            return "Not linked"
        source = obj.data_source.get_real_instance()
        return f"{source.name} ({source.status})"


class StudyAssetInline(admin.TabularInline):
    model = StudyAsset
    extra = 1
    fields = ['name', 'file']
    verbose_name = 'Study Asset'
    verbose_name_plural = 'Study Assets'


class SourceConfigurationInline(admin.TabularInline):
    model = StudySourceConfiguration
    form = SourceConfigurationInlineForm
    extra = 0
    fields = ['source_type', 'status', 'data_start', 'data_end', 'change_link']
    readonly_fields = ['change_link']

    @admin.display(description='')
    def change_link(self, obj):
        if not obj.pk:
            return '-'
        url = reverse('admin:studies_studysourceconfiguration_change', args=[obj.pk])
        return format_html('<a href="{}">Edit</a>', url)


@admin.register(StudySourceConfiguration)
class StudySourceConfigurationAdmin(admin.ModelAdmin):
    form = StudySourceConfigurationForm
    list_display = ('study', 'source_type', 'status')
    readonly_fields = ()
    can_delete = False
    fields = ('source_type', 'status', 'requested_data_types', 'configuration', 'consent_template_html')
    formfield_overrides = {
        models.JSONField: {'form_class': PrettyJSONFormField},
    }

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == 'consent_template_html':
            kwargs['widget'] = AceWidget(mode='html', theme='monokai', width='100%', height='300px')
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def get_fields(self, request, obj=None):
        # A source type may register a config form that adds type-specific fields
        # (e.g. niimport's variant selector). Use the form's field list when present.
        config_form = DataSource.get_config_form_class_for_type(obj.source_type) if obj else None
        if config_form:
            return [field for field in config_form.base_fields if field != 'study']
        return super().get_fields(request, obj)

    def get_form(self, request, obj=None, **kwargs):
        config_form = DataSource.get_config_form_class_for_type(obj.source_type) if obj else None
        if config_form:
            kwargs['form'] = config_form
        return super().get_form(request, obj, **kwargs)

    def save_model(self, request, obj, form, change):
        # Standalone source configuration add/edit is scoped to the single-study setup.
        if not obj.study_id:
            obj.study = Study.objects.first()
        super().save_model(request, obj, form, change)


@admin.register(Study)
class StudyAdmin(admin.ModelAdmin):
    form = StudyAdminForm
    list_display = ('title',)
    filter_horizontal = ('researchers',)
    inlines = [StudyAssetInline, SourceConfigurationInline, StudyParticipantInline]
    
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name in (
            'study_page_html',
            'join_confirmation_html',
            'registration_pending_html',
        ):
            kwargs['widget'] = AceWidget(mode='html', theme='monokai', width='100%', height='300px')
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        
        return qs.filter(researchers=request.user.profile)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.user = request.user
        form.base_fields['researchers'].widget.can_add_related = False
        form.base_fields['researchers'].widget.can_delete_related = False
        return form
    
    def has_add_permission(self, request):
        # Enforce single study per deployment
        if Study.objects.exists():
            return False
        return super().has_add_permission(request)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            obj.researchers.add(request.user.profile)


class VersionedTextAdmin(admin.ModelAdmin):
    """Base admin for versioned legal text. Access is governed by the standard
    Django model permissions (see the Researchers group in studies/apps.py).

    Saving always creates a new version (see VersionedText.save); past
    versions remain visible in the list and are editable only as read-only
    references."""

    list_display = ('pk', 'created')
    ordering = ('-pk',)
    readonly_fields = ('created',)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == 'text':
            kwargs['widget'] = AceWidget(mode='html', theme='monokai', width='100%', height='300px')
        return super().formfield_for_dbfield(db_field, request, **kwargs)


@admin.register(PrivacyNotice)
class PrivacyNoticeAdmin(VersionedTextAdmin):
    pass


@admin.register(TermsOfService)
class TermsOfServiceAdmin(VersionedTextAdmin):
    pass


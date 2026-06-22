from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.template import engines
from django.contrib import messages
from django.template.loader import get_template
from django.utils.safestring import mark_safe
from django.urls import reverse
from urllib.parse import urlencode
from django.http import JsonResponse
from django.utils import timezone
from django.apps import apps
from django.utils import timezone

from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.authentication import TokenAuthentication, SessionAuthentication
from rest_framework.permissions import IsAuthenticated

from study_server.utils import data_to_csv_response
import base64
from .models import Study, Consent, StudyParticipant, StudySourceConfiguration
from .forms import ConsentAcceptanceForm, DataSourceSelectionForm
from . import services
from django.db import transaction
from data_sources.models import DataSource, DeletableWatermark


def get_study():
    study = Study.objects.first()
    if study is None:
        from django.http import Http404
        raise Http404("No study configured.")
    return study



@login_required
def join_study(request):
    study = get_study()
    profile = request.user.profile

    study_participant, _ = StudyParticipant.objects.get_or_create(
        participant=profile,
        study=study,
    )

    def ensure_consent(source_configuration, is_optional):
        # Idempotent: don't duplicate an active consent if the participant
        # re-enters the join flow. Revoked consents are ignored so a participant
        # who withdrew can re-join and get a fresh consent.
        already = Consent.objects.filter(
            participant=profile,
            study=study,
            source_configuration=source_configuration,
            revocation_date__isnull=True,
        ).exists()
        if already:
            return
        Consent.objects.create(
            participant=profile,
            study=study,
            source_configuration=source_configuration,
            source_type=source_configuration.source_type,
            is_optional=is_optional,
            study_participant=study_participant,
        )

    for source_configuration in study.source_config_entries.filter(status='required'):
        ensure_consent(source_configuration, is_optional=False)

    for source_configuration in study.source_config_entries.filter(status='optional'):
        ensure_consent(source_configuration, is_optional=True)
        
    messages.info(request, f"You have started the enrollment process for '{study.title}'. Please complete the required steps.")
    return redirect('consent_workflow')


@login_required
def withdraw_from_study(request):
    study = get_study()
    profile = request.user.profile

    if request.method == 'POST':
        consents = Consent.objects.filter(
            participant=profile,
            study=study,
            revocation_date__isnull=True
        )
        for consent in consents:
            consent.data_source = None
            consent.revocation_date = timezone.now()
            consent.is_complete = False
            consent.save()
        messages.success(request, f"You have successfully withdrawn from the study '{study.title}'.")
        return redirect('dashboard')
    
    return render(request, 'studies/withdraw.html', {'study': study})


@login_required
def revoke_consent(request, consent_id):
    profile = request.user.profile
    consent = get_object_or_404(Consent, id=consent_id, participant=profile, revocation_date__isnull=True)
    study = consent.study

    if not consent.is_optional:
        messages.info(request, "This consent is mandatory for the study. To withdraw, please use the withdrawal page.")
        return redirect('withdraw_from_study')

    if request.method == 'POST':
        consent.data_source = None
        consent.revocation_date = timezone.now()
        consent.is_complete = False
        consent.save()
        # Create a new empty consent for the same data source type
        Consent.objects.create(
            participant=profile,
            study=study,
            source_configuration=consent.source_configuration,
            source_type=consent.source_type,
            is_optional=True,
            study_participant=consent.study_participant,
        )
        messages.success(request, f"You have revoked consent for {consent.source_type} in '{study.title}'.")
        return redirect('dashboard')

    return render(request, 'studies/revoke_consent.html', {'study': study, 'consent': consent})


def study_detail(request):
    study = get_study()
    html_content = study.study_page_html
    if not html_content:
        html_content = services.get_study_page_html(study.raw_content_base_url)
    user_in_study = False
    if request.user.is_authenticated and hasattr(request.user, 'profile'):
        user_in_study = Consent.objects.filter(
            participant=request.user.profile,
            study=study,
            revocation_date__isnull=True
        ).exists()

    template = engines['django'].from_string(html_content)
    
    context = {
        'study': study,
        'request': request,
        'user': request.user,
        'user_in_study': user_in_study,
        'config_repository': study.raw_content_base_url,
        'join_or_login_section': "studies/study_page_components/join_or_login_section.html",
        'assets': study.get_assets_dict(),
    }
    return render(request, 'studies/study_detail_wrapper.html', {'study_page_content': template.render(context)})



def get_next_consent(profile, study, consent_id=None):
    if consent_id:
        return get_object_or_404(
            Consent,
            id=consent_id,
            participant=profile,
            study=study,
            revocation_date__isnull=True
        )
    return Consent.objects.filter(
        participant=profile,
        study=study,
        is_complete=False,
        is_optional=False,
        revocation_date__isnull=True
    ).first()


def consent_checkbox_view(request, consent, study):
    source_type = consent.source_configuration.source_type if consent.source_configuration else consent.source_type
    html_template = services.get_consent_template(study, source_type, consent.source_configuration)
    template = engines['django'].from_string(html_template)
    source_configuration = consent.source_configuration
    if source_configuration:
        model_cls = source_configuration.get_data_source_class()
        if model_cls:
            source_type_label = model_cls.display_type_for_configuration(source_configuration.configuration)
        else:
            source_type_label = source_configuration.source_type.replace('_', ' ').title()
    else:
        source_type_label = consent.source_type

    if request.method == 'POST':
        form = ConsentAcceptanceForm(request.POST)
        if form.is_valid():
            consent.accept_configuration()
            if consent.data_source:
                consent.complete_with_source(consent.data_source)
            else:
                consent.save()
            return redirect(f"{reverse('consent_workflow')}?consent_id={consent.id}")
    else:
        form = ConsentAcceptanceForm()

    checkbox_template = get_template('studies/consent_checkbox_form.html')
    consent_form_html = checkbox_template.render({'form': form}, request)
        
    context = {
        'consent': consent,
        'study': study,
        'consent_form': mark_safe(consent_form_html),
        'request': request,
        'source_type_label': source_type_label,
    }
    rendered = template.render(context)
    return render(request, 'studies/consent_wrapper.html', {
        'content': rendered,
        'scroll_to': 'consent-form'
    })


def create_data_source_flow(consent):
    source_configuration = consent.source_configuration
    if not source_configuration:
        from django.http import Http404
        raise Http404("Missing source configuration for consent.")
    base_url = reverse('add_data_source', args=[source_configuration.source_type])
    query_params = urlencode({
        'consent_id': consent.id,
        'source_configuration_id': source_configuration.id,
    })
    return redirect(f'{base_url}?{query_params}')


def get_existing_sources_for_consent(profile, consent):
    model_cls = DataSource.get_class_for_type(consent.source_type)
    if not model_cls:
        return []

    sources = profile.data_sources.filter(
        polymorphic_ctype__model=model_cls.__name__.lower(),
    )
    # Match against the consent's snapshotted configuration (what the participant
    # agreed to). _matches_configuration returns True for empty config, so this
    # safely disambiguates multiple sources of the same type (e.g. niimport variants).
    return [source for source in sources if source._matches_configuration(consent.configuration or {})]


def select_data_source_view(request, consent, profile, study):
    source_type = consent.source_configuration.source_type if consent.source_configuration else consent.source_type
    html_template = services.get_consent_template(study, source_type, consent.source_configuration)
    template = engines['django'].from_string(html_template)
    source_configuration = consent.source_configuration
    if source_configuration:
        model_cls = source_configuration.get_data_source_class()
        if model_cls:
            source_type_label = model_cls.display_type_for_configuration(source_configuration.configuration)
        else:
            source_type_label = source_configuration.source_type.replace('_', ' ').title()
    else:
        source_type_label = source_type

    available_sources = get_existing_sources_for_consent(profile, consent)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'select':
            form = DataSourceSelectionForm(request.POST, available_sources=available_sources)
            if form.is_valid() and form.cleaned_data['source_id']:
                source = profile.data_sources.filter(id=form.cleaned_data['source_id']).first()
                # Re-validate against the matched set: a POSTed source_id must
                # actually match this consent's configuration, not just belong to
                # the participant.
                if source and any(source.id == candidate.id for candidate in available_sources):
                    consent.complete_with_source(source)
                    return redirect('consent_workflow')
                messages.error(request, "That data source does not match what this consent requires.")
        elif action == 'create':
            if not consent.source_configuration:
                from django.http import Http404
                raise Http404("Missing source configuration for consent.")
            base_url = reverse('add_data_source', args=[consent.source_configuration.source_type])
            query_params = urlencode({
                'consent_id': consent.id,
                'source_configuration_id': consent.source_configuration.id,
            })
            return redirect(f'{base_url}?{query_params}')
    else:
        form = DataSourceSelectionForm(available_sources=available_sources)

    source_form_template = get_template('studies/source_selection_form.html')
    source_form_html = source_form_template.render({'form': form, 'consent': consent}, request)
    
    context = {
        'consent': consent,
        'study': study,
        'consent_form': source_form_html,
        'source_type_label': source_type_label,
    }
    rendered = template.render(context)
    return render(request, 'studies/consent_wrapper.html', {
        'content': rendered,
        'scroll_to': 'consent-form'
    })

@login_required
def consent_workflow(request):
    study = get_study()
    profile = request.user.profile
    consent_id = request.GET.get('consent_id')
    consent = get_next_consent(profile, study, consent_id)

    if not consent:
        messages.success(request, f"All required sources set up for '{study.title}'")
        return redirect('dashboard')
    
    # step 1: show the consent checkbox
    if not consent.consent_text_accepted:
        return consent_checkbox_view(request, consent, study)
    
    # step 2: select or create data source
    available_sources = get_existing_sources_for_consent(profile, consent)
    if not available_sources:
        return create_data_source_flow(consent)
    else:
        return select_data_source_view(request, consent, profile, study)

def _clean_row(row):
    for k, v in row.items():
        if isinstance(v, bytes):
            try:
                row[k] = v.decode('utf-8')
            except Exception:
                row[k] = base64.b64encode(v).decode('ascii')
    return row

def _parse_date(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d") if date_str else None

def _make_timezone_aware(dt):
    if dt is not None and timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt

def _allowed_data_types(consent):
    """Data types the participant consented to share, or None when unrestricted.

    ``requested_data_types`` is a comma-separated snapshot taken at consent time;
    an empty value means no restriction (all of the source's types).
    """
    raw = (consent.requested_data_types or '').strip()
    if not raw:
        return None
    return {t.strip() for t in raw.split(',') if t.strip()}


@api_view(['GET'])
@authentication_classes([TokenAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def study_data_api(request):
    study = Study.objects.first()
    if study is None:
        return JsonResponse({'error': 'No study configured'}, status=404)

    if not request.user.is_superuser:
        if not study.researchers.filter(user=request.user).exists():
            return JsonResponse({'error': 'Unauthorized'}, status=403)

    data_type = request.GET.get('data_type')

    active_consents = Consent.objects.filter(
        study=study,
        is_complete=True,
        revocation_date__isnull=True,
        data_source__status='active'
    ).select_related('data_source', 'study_participant').order_by('id')

    # Collect all available data types across consents, restricted to what each
    # participant consented to share (requested_data_types snapshot).
    all_data_types = set()
    for consent in active_consents:
        if not consent.data_source:
            continue
        source = consent.data_source.get_real_instance()
        available = set(source.get_data_types())
        allowed = _allowed_data_types(consent)
        if allowed is not None:
            available &= allowed
        all_data_types.update(available)

    if not data_type:
        return JsonResponse({
            'study': study.title,
            'data_types': sorted(all_data_types),
        })

    try:
        output_format = request.GET.get('format', 'json')
        limit = int(request.GET.get('limit', 10000))
        offset = int(request.GET.get('offset', 0))

        start_date_param = request.GET.get('start_date')
        end_date_param = request.GET.get('end_date')
        start_date = _parse_date(start_date_param)
        end_date = _parse_date(end_date_param)
    except:
        return JsonResponse({'error': 'Invalid query parameters'}, status=400)

    if offset < 0 or limit < 1:
        return JsonResponse({'error': 'Invalid limit or offset'}, status=400)
    if limit > 10000:
        return JsonResponse({'error': 'Maximum limit is 10000'}, status=400)

    all_data = []
    full_count = 0
    for consent in active_consents:
        if not consent.data_source:
            continue
        source = consent.data_source.get_real_instance()
        data_types = source.get_data_types()

        if data_type not in data_types:
            continue

        # Never return a data type the participant did not consent to share.
        allowed = _allowed_data_types(consent)
        if allowed is not None and data_type not in allowed:
            continue

        consent_start = consent.data_start or consent.consent_date
        if not consent_start:
            continue

        consent_end = consent.revocation_date or timezone.now()

        type_start = consent.data_start
        type_end = consent.data_end

        effective_start = type_start or consent_start
        start_candidates = [_make_timezone_aware(d) for d in [effective_start, start_date] if d is not None]
        interval_start = max(start_candidates) if start_candidates else None

        end_candidates = [_make_timezone_aware(d) for d in [type_end, consent_end, end_date] if d is not None]
        interval_end = min(end_candidates) if end_candidates else None

        count = source.count_rows(
            data_type=data_type,
            start_date=interval_start,
            end_date=interval_end
        )
        full_count += count

        if limit <= 0:
            continue

        if offset > count:
            # This source is completely skipped
            offset -= count
            if offset < 0:
                offset = 0
            continue

        data = source.fetch_data(
            data_type=data_type,
            start_date=interval_start,
            end_date=interval_end,
            limit=limit,
            offset=offset,
        )

        # Update the downloaded_through watermark for this (data_source, data_type).
        if isinstance(data, list):
            max_ts = None
            for r in data:
                ts_raw = r.get('timestamp')
                if ts_raw is None:
                    continue
                try:
                    ts = int(ts_raw)
                except (TypeError, ValueError):
                    continue
                if max_ts is None or ts > max_ts:
                    max_ts = ts
            if max_ts is not None:
                wm, _ = DeletableWatermark.objects.get_or_create(
                    data_source=consent.data_source, data_type=data_type
                )
                with transaction.atomic():
                    wm = DeletableWatermark.objects.select_for_update().get(pk=wm.pk)
                    if wm.downloaded_through is None or max_ts > wm.downloaded_through:
                        wm.downloaded_through = max_ts
                        wm.save(update_fields=['downloaded_through', 'updated_at'])

        limit -= len(data)
        for row in data:
            row["data_type"] = data_type
            row["source_type"] = consent.source_type
            # For niimport, the variant (google/tiktok/...) lives in the snapshot; expose it.
            row["origin"] = (consent.configuration or {}).get('niimport_source_type')
            row["participant_id"] = str(consent.study_participant.pseudo_id) if consent.study_participant else None
            all_data.append(_clean_row(row))

        offset = 0

    if output_format == 'csv':
        return data_to_csv_response(all_data, "study_data.csv")
    else:
        return JsonResponse({
            'study': study.title,
            'data_count': full_count,
            'data_types': [data_type],
            'data': all_data
        })
    
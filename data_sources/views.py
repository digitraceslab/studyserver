import importlib
from django.apps import apps
from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.contrib import messages
from django.utils import timezone
from urllib.parse import urlencode
from . import forms
from .forms import JsonUrlDataSourceForm, AwareDataSourceForm, DataFilterForm
from .models import DataSource, AwareDataSource, JsonUrlDataSource
from studies.models import Consent
from datetime import date, datetime, time, timedelta
import zoneinfo

from urllib.parse import urlencode


def form_has_only_name_field(form):
    fields = list(form.fields.keys())
    return fields == ['name']

def source_default_title(source_title, consent_id=None, profile = None):
    default_name = f"{source_title} Source"
    if consent_id:
        consent = Consent.objects.filter(
            id=consent_id,
            participant=profile
        ).first()
        if consent:
            study = consent.study
            default_name += f" for {study.title}"
    return default_name

def link_consent_to_source(consent_id, data_source, profile):
    consent = Consent.objects.filter(
        id=consent_id,
        participant=profile
    ).first()
    if consent:
        consent.data_source = data_source
        consent.is_complete = True
        consent.consent_date = timezone.now()
        source_start, _ = consent.study.get_source_dates(consent.source_type)
        consent.data_start = source_start or consent.consent_date
        consent.save()


@login_required
def select_data_source_type(request):
    source_types = []
    all_models = apps.get_app_config('data_sources').get_models()
    for model in all_models:
        if model._meta.proxy:
             continue
        if issubclass(model, DataSource) and model is not DataSource:
            type_name = model.__name__.replace('DataSource', '')
            source_types.append(type_name)
            
    return render(request, 'data_sources/select_source_type.html', {'types': source_types})


def add_data_source(request, source_type):
    consent_id = request.GET.get('consent_id')

    # Dynamically get the Model and Form classes
    ModelClass = DataSource.get_class_for_type(source_type)
    FormClass = DataSource.get_form_class_for_type(source_type)
    
    if not ModelClass or not FormClass:
        raise Http404("Data source does not have a valid model and form class.")

    source_title = source_type.replace('_', ' ')
    default_name = source_default_title(source_title, consent_id, request.user.profile)
    
    if request.method == 'GET':
        form = FormClass(initial={'name': default_name})
        if form_has_only_name_field(form):
            # Create a form that is already filled, no user input needed
            form = FormClass({'name': default_name})
        else:
            # Actually render the form and wait for a post
            return render(
                request,
                'data_sources/add_data_source.html',
                {
                    'form': form,
                    'title': f"Add {source_title.title()} Source"
                }
            )
    else:
        # Normal post, create the form from posted data
        form = FormClass(request.POST)

    if form.is_valid():
        new_source = form.save(commit=False)
        new_source.profile = request.user.profile
        if not new_source.requires_setup and not new_source.requires_confirmation:
            new_source.status = 'active'
        new_source.save()

        # Link to consent if coming from consent workflow
        if consent_id:
            link_consent_to_source(consent_id, new_source, request.user.profile)
        

        if new_source.requires_setup:
            try:
                base_url = new_source.get_setup_url()
            except Exception as e:
                messages.error(request, f"Failed to set up data source: {e}")
                new_source.delete()
                return redirect('dashboard')
            if consent_id:
                query_params = urlencode({'consent_id': consent_id})
                return redirect(f'{base_url}?{query_params}')
            else:
                return redirect(base_url)
        else:
            messages.success(request, f"Successfully added data source: {new_source.name}")
            if consent_id:
                consent = Consent.objects.filter(
                    id=consent_id,
                    participant=request.user.profile
                ).first()
                return redirect('consent_workflow', study_id=consent.study.id)
            else:
                return redirect('dashboard')
    
    # Invalid form, re-render
    title = f"Add {source_type.replace('_', ' ').title()} Source"
    return render(request, 'data_sources/add_data_source.html', {'form': form, 'title': title})


@require_POST
@login_required
def delete_data_source(request, source_id):
    source = get_object_or_404(DataSource, id=source_id, profile=request.user.profile)
    real_source = source.get_real_instance()
    source_name = source.name

    active_consents = Consent.objects.filter(
        data_source=source,
        revocation_date__isnull=True
    )
    
    if active_consents.exists():
        studies_list = ', '.join([c.study.title for c in active_consents])
        messages.error(
            request, 
            f"Cannot delete '{source_name}': Still linked to active studies: {studies_list}. "
            f"Withdraw from those studies first."
        )
        return redirect('dashboard')

    if hasattr(real_source, 'revoke_before_delete'):
        real_source.revoke_before_delete()

    Consent.objects.filter(data_source=source).update(data_source=None)

    source.delete()
    messages.success(request, f"Successfully deleted data source: {source_name}")
    return redirect('dashboard')


@login_required
def view_data_source(request, source_id):
    source = get_object_or_404(DataSource, id=source_id, profile=request.user.profile)
    real_instance = source.get_real_instance()

    data_types = real_instance.get_data_types()
    if request.GET:
        form = DataFilterForm(request.GET, data_type_choices=data_types)
    else:
        initial_data = {'start_date': date.today(), 'end_date': date.today()}
        form = DataFilterForm(initial=initial_data, data_type_choices=data_types)

    headers = []
    page_obj = None
    if form.is_valid():
        selected_type = form.cleaned_data.get('data_type')
        start_date = form.cleaned_data.get('start_date')
        end_date = form.cleaned_data.get('end_date')

        if selected_type:
            tz = zoneinfo.ZoneInfo('Europe/Helsinki')
            start_datetime = datetime.combine(start_date, time.min, tzinfo=tz) if start_date else None
            end_datetime = datetime.combine(end_date, time.max, tzinfo=tz) if end_date else None
            all_data = real_instance.fetch_data(
                data_type=selected_type, 
                start_date=start_datetime,
                end_date=end_datetime
            )

            if all_data:
                headers = all_data[0].keys()
                
                paginator = Paginator(all_data, 100)
                page_number = request.GET.get('page')
                page_obj = paginator.get_page(page_number)

    context = {
        'source': real_instance,
        'form': form,
        'headers': headers,
        'page_obj': page_obj,
    }
    return render(request, 'data_sources/data_source_detail.html', context)


@login_required
def edit_data_source(request, source_id):
    source = get_object_or_404(DataSource, id=source_id, profile=request.user.profile)
    real_instance = source.get_real_instance()

    # Select the correct form based on the model's class
    if isinstance(real_instance, AwareDataSource):
        FormClass = AwareDataSourceForm
    elif isinstance(real_instance, JsonUrlDataSource):
        FormClass = JsonUrlDataSourceForm
    else:
        messages.error(request, "This data source type cannot be edited.")
        return redirect('dashboard')

    if request.method == 'POST':
        form = FormClass(request.POST, instance=real_instance)
        if form.is_valid():
            form.save()
            messages.success(request, f"Successfully updated '{real_instance.name}'.")
            return redirect('dashboard')
    else:
        form = FormClass(instance=real_instance)

    return render(
        request, 
        'data_sources/add_data_source.html', 
        {'form': form, 'title': f'Edit "{real_instance.name}"'}
    )


@login_required
def instructions(request, source_id):
    source = get_object_or_404(DataSource, id=source_id, profile=request.user.profile)
    real_source = source.get_real_instance()

    consent_id = request.GET.get('consent_id')
    study_id = None
    if consent_id in [None, '', 'None']:
        consent = Consent.objects.filter(
            id=consent_id, 
            participant=request.user.profile
        ).first()
        if consent:
            study_id = consent.study.id
        else:
            study_id = None
    
    context, template = real_source.get_instructions_card(request, consent_id, study_id)

    return render(request,
        'data_sources/instructions_wrapper.html',
        {
            'instructions_context': context,
            'instructions_template': template,
        }
    )


@login_required
def confirm_data_source(request, source_id):
    source = get_object_or_404(DataSource, id=source_id, profile=request.user.profile)
    real_source = source.get_real_instance()

    success, message = real_source.confirm(request)

    if not success:
        messages.error(request, message)
        return redirect('dashboard')
    
    messages.success(request, message)
    return redirect('dashboard')


def token_view_dispatcher(request, token, view_type):
    source = get_object_or_404(DataSource, config_token=token)
    real_source = source.get_real_instance()
    
    return real_source.handle_token_view(request, token, view_type)


@login_required
def auth_start(request, source_id):
    source = get_object_or_404(DataSource, id=source_id, profile=request.user.profile)
    real_source = source.get_real_instance()

    auth_url = real_source.get_auth_url(request)
    return redirect(auth_url)


@login_required
def auth_callback(request):
    state = request.GET.get('state')
    error = request.GET.get('error')

    if error:
        messages.error(request, f"Authorization failed: {error}")
        return redirect('dashboard')
    
    if not state:
        messages.error(request, "Authorization failed: Missing state parameter.")
        return redirect('dashboard')

    source = DataSource.objects.filter(
        profile=request.user.profile,
        oauth_state=state
    ).first()
    real_source = source.get_real_instance()

    if real_source:
        success, message = real_source.handle_auth_callback(request)


    return redirect('dashboard')


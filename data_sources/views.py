"""Participant-facing views for data sources, mounted under /data-sources/.

Unless a view says otherwise: it requires login (anonymous users are
redirected to the login page), it acts only on a source owned by the
logged-in user's profile (for any other or unknown ``source_id``,
``get_object_or_404`` raises Http404, which Django turns into a 404),
and it accepts any HTTP method. POST requests go through Django's CSRF
check (403 without a valid token). Outcomes are reported with Django
messages, usually followed by a redirect to the dashboard.
"""

from django.apps import apps
from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from urllib.parse import urlencode
from .forms import JsonUrlDataSourceForm, AwareDataSourceForm, DataFilterForm
from .models import DataSource, AwareDataSource, JsonUrlDataSource
from studies.models import Consent, StudySourceConfiguration
from datetime import date, datetime, time
import zoneinfo

from urllib.parse import urlencode


def _form_has_only_name_field(form):
    """Check whether ``name`` is the form's only enabled field.

    Args:
        form: The form to inspect.

    Returns:
        True when ``name`` is the only field that is not disabled.
    """
    return [name for name, field in form.fields.items() if not field.disabled] == ['name']


def _source_default_title(source_title, consent_id=None, profile = None):
    """Build the default source name: "<title> Source".

    Args:
        source_title: Title of the source type.
        consent_id: Optional consent id; when it is a consent of ``profile``,
            " for <study title>" is appended.
        profile: Profile that must own the consent.

    Returns:
        The default name.
    """
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

def _link_consent_to_source(consent_id, data_source, profile):
    """Complete ``profile``'s consent ``consent_id`` with ``data_source``.

    Does nothing when the consent does not exist or belongs to someone else.

    Args:
        consent_id: Id of the consent to complete.
        data_source: Source to complete the consent with.
        profile: Profile that must own the consent.
    """
    consent = Consent.objects.filter(
        id=consent_id,
        participant=profile
    ).first()
    if consent:
        consent.complete_with_source(data_source)


@login_required
def select_data_source_type(request):
    """GET /data-sources/add/: page listing the source types to add.

    Renders ``data_sources/select_source_type.html`` with ``types`` (slug
    and label per type). Lists only types defined in the data_sources app,
    so types from other apps (survey) are not offered.
    """
    source_types = []
    all_models = apps.get_app_config('data_sources').get_models()
    for model in all_models:
        if model._meta.proxy:
             continue
        if issubclass(model, DataSource) and model is not DataSource and model.SOURCE_TYPE:
            source_types.append({
                'slug': model.SOURCE_TYPE,
                'label': model.SOURCE_TYPE.replace('_', ' ').title(),
            })

    return render(request, 'data_sources/select_source_type.html', {'types': source_types})


@login_required
def add_data_source(request, source_type):
    """/data-sources/add/<source_type>/: create a source of that type for the user.

    Query parameters (kept in the form's POST URL): ``consent_id``, a consent of the user
    to complete with the new source; ``source_configuration_id``, a
    StudySourceConfiguration of this type whose configuration is passed to
    the form. The configuration is not checked against the user's studies
    or consents. The new source copies configuration, data dates and
    requested data types from the consent, else from the configuration.

    POST creates the source. GET creates it at once, with the default name,
    when the form has only a name field (other fields disabled) and
    ``consent_id`` is a consent of the user with no data source yet. The
    new source is linked to the consent; its status is "active" unless the
    type needs setup or confirmation.

    Args:
        source_type: Source type slug (``SOURCE_TYPE``) from the URL.

    Returns:
        - Any other GET: ``data_sources/add_data_source.html`` with the
          default name filled in (for a name-only form, a confirmation
          page).
        - Invalid POST data: the form re-rendered (200) with the errors as
          messages.
        - After creating, when ``requires_setup``: a redirect to
          ``get_setup_url()`` (with ``?consent_id=``), or, if that raises,
          the source is deleted and a redirect to the dashboard.
        - After creating otherwise: a redirect to ``consent_workflow``
          (with a consent) or the dashboard.

    Raises:
        Http404: Unknown type or no registered form.
    """
    consent_id = request.GET.get('consent_id')
    source_configuration_id = request.GET.get('source_configuration_id')
    source_configuration = None

    # Dynamically get the Model and Form classes
    ModelClass = DataSource.get_class_for_type(source_type)
    FormClass = DataSource.get_form_class_for_type(source_type)
    
    if not ModelClass or not FormClass:
        raise Http404("Data source does not have a valid model and form class.")

    source_title = source_type.replace('_', ' ')
    default_name = _source_default_title(source_title, consent_id, request.user.profile)

    form_configuration = {}
    if source_configuration_id:
        source_configuration = StudySourceConfiguration.objects.filter(
            id=source_configuration_id,
            source_type=source_type,
        ).first()
        if source_configuration:
            form_configuration = source_configuration.configuration or {}
    
    if request.method == 'GET':
        form_kwargs = {'initial': {'name': default_name}}
        if form_configuration:
            form_kwargs['configuration'] = form_configuration
        form = FormClass(**form_kwargs)
        open_consent = consent_id and Consent.objects.filter(
            id=consent_id, participant=request.user.profile, data_source__isnull=True
        ).exists()
        if open_consent and _form_has_only_name_field(form):
            # No user input needed: fill the form and create at once
            form = FormClass({'name': default_name}, **({k: v for k, v in form_kwargs.items() if k != 'initial'}))
        else:
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
        post_kwargs = {}
        if form_configuration:
            post_kwargs['configuration'] = form_configuration
        form = FormClass(request.POST, **post_kwargs)

    if form.is_valid():
        new_source = form.save(commit=False)
        new_source.profile = request.user.profile
        consent = Consent.objects.filter(id=consent_id, participant=request.user.profile).first() if consent_id else None
        param_source = consent or source_configuration
        new_source.configuration = dict(consent.configuration) if consent else dict(form_configuration)
        if param_source:
            new_source.data_start = param_source.data_start.date() if param_source.data_start else None
            new_source.data_end = param_source.data_end.date() if param_source.data_end else None
            new_source.configuration['requested_data_types'] = param_source.requested_data_types
        if not new_source.requires_setup and not new_source.requires_confirmation:
            new_source.status = 'active'
        new_source.save()

        # Link to consent if coming from consent workflow
        if consent_id:
            _link_consent_to_source(consent_id, new_source, request.user.profile)
        

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
                return redirect('consent_workflow')
            else:
                return redirect('dashboard')
    
    # Invalid form, re-render
    for errors in form.errors.values():
        for error in errors:
            messages.error(request, error)
    title = f"Add {source_type.replace('_', ' ').title()} Source"
    return render(request, 'data_sources/add_data_source.html', {'form': form, 'title': title})


@require_POST
@login_required
def delete_data_source(request, source_id):
    """POST /data-sources/<source_id>/delete/: delete the user's source.

    Other methods get 405, checked before login. Refused (error message)
    while any unrevoked consent links to the source. Otherwise calls
    ``revoke_before_delete()``, unlinks revoked consents and deletes the
    row.

    Args:
        source_id: Id of the source to delete, from the URL.

    Returns:
        A redirect to the dashboard, always.
    """
    source =get_object_or_404(DataSource, id=source_id, profile=request.user.profile)
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
    """GET /data-sources/<source_id>/: browse the user's own data, 100 rows a page.

    Query parameters: ``data_type`` (one of ``get_data_types()``),
    ``start_date`` / ``end_date`` (dates, Europe/Helsinki; the form
    defaults to today), and ``cursor`` (Unix ms, from the next-page link).
    Invalid filters are shown as messages. Calls ``fetch_data`` with the base
    cursor signature, so it raises (500) for ``NiimportDataSource``, and
    treats a non-list result as no rows. Does not check consent.

    Args:
        source_id: Id of the source to browse, from the URL.

    Returns:
        ``data_sources/data_source_detail.html`` rendered with the rows,
        headers (the first row's keys), total count and next cursor.
    """
    source =get_object_or_404(DataSource, id=source_id, profile=request.user.profile)
    real_instance = source.get_real_instance()

    data_types = real_instance.get_data_types()
    if request.GET:
        form = DataFilterForm(request.GET, data_type_choices=data_types)
    else:
        initial_data = {'start_date': date.today(), 'end_date': date.today()}
        form = DataFilterForm(initial=initial_data, data_type_choices=data_types)

    PAGE_SIZE = 100
    headers = []
    rows = []
    total_count = 0
    num_pages = 0
    has_next = False
    next_cursor = None

    if form.is_valid():
        selected_type = form.cleaned_data.get('data_type')
        start_date = form.cleaned_data.get('start_date')
        end_date = form.cleaned_data.get('end_date')

        if selected_type:
            tz = zoneinfo.ZoneInfo('Europe/Helsinki')
            start_datetime = datetime.combine(start_date, time.min, tzinfo=tz) if start_date else None
            end_datetime = datetime.combine(end_date, time.max, tzinfo=tz) if end_date else None

            start_ms = int(start_datetime.timestamp() * 1000) if start_datetime else 0
            end_ms = int(end_datetime.timestamp() * 1000) if end_datetime else None

            # cursor: explicit ?cursor= overrides; otherwise start of the range.
            try:
                cursor = int(request.GET.get('cursor', start_ms))
            except (TypeError, ValueError):
                cursor = start_ms
            if cursor < start_ms:
                cursor = start_ms

            total_count = real_instance.count_rows(selected_type, start_datetime, end_datetime)
            num_pages = (total_count + PAGE_SIZE - 1) // PAGE_SIZE if total_count else 0

            page_rows = real_instance.fetch_data(selected_type, timestamp=cursor, limit=PAGE_SIZE)
            if not isinstance(page_rows, list):
                page_rows = []
            # cap to the end of the requested range
            if end_ms is not None:
                page_rows = [r for r in page_rows if r.get('timestamp') is None or int(r['timestamp']) <= end_ms]

            rows = page_rows
            if rows:
                headers = list(rows[0].keys())
                if len(rows) >= PAGE_SIZE:
                    candidate = int(rows[-1]['timestamp']) + 1
                    if end_ms is None or candidate <= end_ms:
                        has_next = True
                        next_cursor = candidate
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)

    # Preserve filter params (data_type/start_date/end_date) across page links, dropping any existing cursor.
    _q = request.GET.copy()
    _q.pop('cursor', None)
    base_query = _q.urlencode()

    context = {
        'source': real_instance,
        'form': form,
        'headers': headers,
        'rows': rows,
        'total_count': total_count,
        'num_pages': num_pages,
        'has_next': has_next,
        'next_cursor': next_cursor,
        'base_query': base_query,
    }
    return render(request, 'data_sources/data_source_detail.html', context)


@login_required
def edit_data_source(request, source_id):
    """/data-sources/<source_id>/edit/: edit the user's AWARE or JSON URL source.

    Other types get an error message and a redirect to the dashboard.
    GET renders the form in ``data_sources/add_data_source.html``. A valid
    POST saves (AWARE: name; JSON URL: name and URL) and redirects to the
    dashboard; an invalid one re-renders the form (200).

    Args:
        source_id: Id of the source to edit, from the URL.
    """
    source =get_object_or_404(DataSource, id=source_id, profile=request.user.profile)
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
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    else:
        form = FormClass(instance=real_instance)

    return render(
        request, 
        'data_sources/add_data_source.html', 
        {'form': form, 'title': f'Edit "{real_instance.name}"'}
    )


@login_required
def instructions(request, source_id):
    """GET /data-sources/instructions/<source_id>/: setup instructions page.

    Renders the source's ``get_instructions_card`` inside
    ``data_sources/instructions_wrapper.html``. The optional ``consent_id``
    query parameter is passed through unchecked. ``study_id`` is always
    None: the consent lookup only runs when ``consent_id`` is empty. Types
    without an instructions card (only AWARE has one) raise TypeError, a
    500.

    Args:
        source_id: Id of the source to show instructions for, from the URL.
    """
    source =get_object_or_404(DataSource, id=source_id, profile=request.user.profile)
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
            'consent_id': consent_id,
        }
    )


@login_required
@require_POST
def confirm_data_source(request, source_id):
    """POST /data-sources/<source_id>/confirm/: run the source's ``confirm``.

    Other methods get 405 (after the login check, so anonymous users are
    redirected to login first). Shows the returned message as success or
    error and always redirects to the dashboard.

    Args:
        source_id: Id of the source to confirm, from the URL.
    """
    source =get_object_or_404(DataSource, id=source_id, profile=request.user.profile)
    real_source = source.get_real_instance()

    success, message = real_source.confirm(request)

    if not success:
        messages.error(request, message)
        return redirect('dashboard')
    
    messages.success(request, message)
    return redirect('dashboard')


def token_view_dispatcher(request, token, view_type):
    """/data-sources/config/<token>/<view_type>/: token-authenticated source pages.

    No login: the source's secret ``config_token`` is the only
    authorization, and any source with that token is used, whoever owns
    it. POSTs need a CSRF token.

    Args:
        token: The source's ``config_token`` (UUID), from the URL.
        view_type: Which token view to show, from the URL; interpreted by
            the source.

    Returns:
        The response of the source's
        ``handle_token_view(request, token, view_type)`` (see
        ``AwareDataSource.handle_token_view``). For an unknown
        ``view_type`` that is None, and Django raises ValueError (500).

    Raises:
        Http404: No source has this token.
        AttributeError: The source type has no ``handle_token_view`` (500).
    """
    source =get_object_or_404(DataSource, config_token=token)
    real_source = source.get_real_instance()
    
    return real_source.handle_token_view(request, token, view_type)


@login_required
def auth_start(request, source_id):
    """/data-sources/oauth/start/<source_id>/: redirect to the source's OAuth page.

    Redirects to ``get_auth_url(request)``. No built-in type defines that
    method, so this currently always gives a 500.

    Args:
        source_id: Id of the source to authorize, from the URL.
    """
    source =get_object_or_404(DataSource, id=source_id, profile=request.user.profile)
    real_source = source.get_real_instance()

    auth_url = real_source.get_auth_url(request)
    return redirect(auth_url)


@login_required
def auth_callback(request):
    """/data-sources/oauth/callback/: OAuth provider redirect target.

    An ``error`` or missing ``state`` query parameter gives an error
    message. Otherwise finds the user's own source whose ``oauth_state``
    equals ``state`` and calls its ``handle_auth_callback(request)``,
    ignoring the result. No matching source gives a 500, as does a type
    without that method (no built-in type has one). Always redirects to
    the dashboard otherwise.
    """
    state =request.GET.get('state')
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


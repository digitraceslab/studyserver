"""Views for participant and researcher accounts, dashboards and the personal data API.

Routes are in ``users.urls`` (under ``/accounts/``), except ``home`` at ``/``.

"Login required" means ``@login_required``: an anonymous user gets a 302 to
the login page with ``?next=<path>``. An authenticated user without a Profile
counts as anonymous (see ``users.middleware.ProfileRequiredMiddleware``).
Unless stated otherwise, views accept any HTTP method and treat non-POST as GET.
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import Group
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.authentication import TokenAuthentication, SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.authtoken.models import Token

from study_server.utils import data_to_csv_response
from django.db import transaction
from users.models import Profile, ProtectedIdentifier
from studies.models import Study, Consent, StudyParticipant
from .forms import CustomUserCreationForm, ParticipantCreationForm
from studies.views import study_detail
from data_sources.models import DataSource



def _safe_next(request):
    """Return the request's `next` parameter (POST, then GET) if it is a safe
    redirect target on this host, else ''. A percent-encoded fragment marker
    (%23) is turned into a real '#'."""
    value = request.POST.get('next') or request.GET.get('next') or ''
    value = value.replace('%23', '#')
    if value and url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return value
    return ''


def login_view(request):
    """Log a user in with Django's AuthenticationForm (URL name `login`).

    GET shows the form; POST takes 'username' and 'password'. Accepts a `next` parameter (GET, or POST via the form's hidden
    field); it is used only if it is a safe URL on this host (same host,
    https required when the request is https), and every '%23' in it is
    turned into '#'. A valid POST logs in and redirects (302) to `next`, or
    to 'home'. Invalid credentials re-render the form (200) with an error
    message.
    """
    next_url = _safe_next(request)
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect(next_url or 'home')
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm(request)
    return render(request, 'users/login.html', {'form': form, 'next': next_url})
    
    
def home(request):
    """Landing page at `/` for the first study.

    GET; no login required. Three outcomes:
    - no study exists: renders 'home.html' (200);
    - logged-in user with an unrevoked consent in Study.objects.first():
      redirects (302) to 'dashboard';
    - otherwise: returns studies.views.study_detail(request) for that study,
      i.e. the public study page.
    """
    study = Study.objects.first()
    if study is None:
        return render(request, 'home.html')

    if request.user.is_authenticated:
        user_in_study = Consent.objects.filter(
            study=study,
            participant=request.user.profile,
            revocation_date__isnull=True
        ).exists()
        if user_in_study:
            return redirect('dashboard')

    return study_detail(request)


def signup(request):
    """Register a participant account.

    GET shows the form; POST takes 'username' (an email address), 'password1'
    and 'password2'. No login required; logged-in users may also sign up.
    The username must be a valid email address whose domain the study allows
    (Study.email_allowed); it is stored lowercased as both username and email.
    When no study exists, signup is closed: every POST fails with
    "Signup is not open yet.".

    A valid POST creates the user and a 'participant' Profile, stores the
    email as a ProtectedIdentifier, and redirects (302) to 'login' without
    logging the user in. Accepts a `next` parameter (GET, or POST via the
    form's hidden field), checked as in login_view; a safe one is passed on
    as 'login?next=...' so the user lands there after logging in. An invalid
    POST re-renders the form (200) with each error as a message.
    """
    next_url = _safe_next(request)
    if request.method == 'POST':
        form = ParticipantCreationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.save()
            profile = Profile.objects.create(
                user=user,
                user_type='participant'
            )
            if user.email:
                ProtectedIdentifier.objects.create(profile=profile, value=user.email)
            login_url = reverse('login')
            if next_url:
                login_url += '?' + urlencode({'next': next_url})
            return redirect(login_url)
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    else:
        form = ParticipantCreationForm()
    return render(request, 'registration/signup.html', {'form': form, 'next': next_url})

def signup_researcher(request):
    """GET shows the form; POST takes the same fields and email rules as signup,
    including closed signup when no study exists. No login required: anyone
    with an allowed email address can register as a researcher.

    A valid POST creates a staff user (is_staff=True, so it can log in to the
    admin) in the 'Researchers' group with a 'researcher' Profile, and
    redirects (302) to 'login' without logging in. `next` is ignored. If the
    'Researchers' group does not exist, the user is already saved and the
    request fails with a 500. An invalid POST re-renders the form (200) with
    each error as a message.
    """
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_staff = True
            user.save()
            
            researcher_group = Group.objects.get(name='Researchers')
            user.groups.add(researcher_group)

            Profile.objects.create(
                user=user,
                user_type='researcher'
            )
            return redirect('login')
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    else:
        form = CustomUserCreationForm()
        
    return render(request, 'registration/signup.html', {'form': form})


@login_required
def manage_token(request):
    """Show the user's API token, creating it if missing.

    Login required; researchers and participants alike. GET renders the page
    (200) with 'token' and 'is_researcher'. A POST containing a 'regenerate'
    field deletes the old token (it stops working at once), creates a new
    one and redirects (302) back here. Any other POST behaves like GET.
    """
    token, created = Token.objects.get_or_create(user=request.user)
    
    if request.method == 'POST' and 'regenerate' in request.POST:
        token.delete()
        token = Token.objects.create(user=request.user)
        messages.success(request, "Token regenerated successfully!")
        return redirect('manage_token')
    
    context = {
        'token': token,
        'is_researcher': request.user.profile.user_type == 'researcher',
    }
    return render(request, 'users/manage_token.html', context)


def _get_past_consents(profile):
    """Return the profile's revoked consents as a list of dicts.

    Each dict has 'consent' (the Consent) and 'type_name', the display name
    from DataSource.display_type_for_source_type(consent.source_type,
    consent.configuration); for an unknown source_type that is the
    title-cased slug.
    """
    past_consents = Consent.objects.filter(
        participant=profile,
        revocation_date__isnull=False
    )
    result = []
    for consent in past_consents:
        display_type = DataSource.display_type_for_source_type(consent.source_type, consent.configuration)
        result.append({
            'consent': consent,
            'type_name': display_type,
        })
    return result


def _get_active_studies(user):
    """Return a queryset of studies where the user has a non-revoked consent."""
    active_consents = Consent.objects.filter(
        participant=user.profile,
        revocation_date__isnull=True
    ).select_related('study')
    study_ids = active_consents.values_list('study_id', flat=True).distinct()
    return Study.objects.filter(id__in=study_ids)


def _get_active_studies_data(profile, request):
    """Group the profile's active consents by study.

    Returns {study: {'active_consents', 'incomplete_consents',
    'incomplete_sources', 'optional_consents'}}, each a list of dicts with
    'consent', 'type_name' and, when a data source exists, 'source' and 'links'.
    Studies whose registration is awaiting required approval are skipped.
    """
    studies_data = {}
    for study in _get_active_studies(profile.user):
        # Hide all consent-related items for a study until the participant's
        # registration is approved. An unapproved participation is surfaced
        # separately as a "registration pending" notice.
        participation = StudyParticipant.objects.filter(
            participant=profile, study=study
        ).first()
        if (
            study.requires_approval
            and participation
            and not participation.researcher_approved
        ):
            continue

        studies_data[study] = {
            'active_consents': [],
            'incomplete_consents': [],
            'incomplete_sources': [],
            'optional_consents': []
        }
        
        consents = Consent.objects.filter(
            participant=profile,
            study=study,
            revocation_date__isnull=True
        )
        for consent in consents:
            consent_data = {
                'consent': consent,
                'type_name': DataSource.display_type_for_source_type(consent.source_type, consent.configuration),
            }
            if consent.data_source:
                source = consent.data_source.get_real_instance()
                consent_data['source'] = source
                consent_data['links'] = consent.data_source.show_links()
            
            if consent.is_optional:
                studies_data[study]['optional_consents'].append(consent_data)
            elif not consent.is_complete:
                studies_data[study]['incomplete_consents'].append(consent_data)
            elif consent.data_source:
                instructions = source.get_instructions_card(request, consent_id=consent.id, study_id=study.id)
                if source.status == 'pending' and instructions:
                    studies_data[study]['incomplete_sources'].append(
                        consent_data
                    )
                else:
                    studies_data[study]['active_consents'].append(
                        consent_data
                    )
    return studies_data

def _get_next_instructions_card(request, studies_data):
    """Find the first incomplete source that requires setup.

    studies_data is the dict from _get_active_studies_data. Returns
    (card_template, card_context) for that source, or (None, None) if none.
    """
    for study, data in studies_data.items():
        for item in data['incomplete_sources']:
            if item['source'].requires_setup:
                card_context, card_template = item['source'].get_instructions_card(
                    request,
                    consent_id=item['consent'].id,
                    study_id=study.id
                )
                return card_template, card_context
    return None, None

@login_required
def dashboard(request):
    """Participant dashboard for the single study.

    GET; login required. Renders 'dashboard.html' (200). Researchers are
    redirected (302) to 'researcher_dashboard'. Context: 'study'
    (Study.objects.first(), or None), 'study_data' (a dict of consent lists
    'active_consents', 'incomplete_consents', 'incomplete_sources' and
    'optional_consents'; None when the user has no unrevoked consent in the
    study, i.e. is not enrolled, or awaits approval), 'pending_participations',
    'past_consents',
    'protected_identifiers', 'collect_tax_id', 'tax_id' and, when a source
    needs setup, 'instructions_template' / 'instructions_context'.
    """
    if request.user.profile.user_type == 'researcher':
        return redirect('researcher_dashboard')

    context = {}
    context['past_consents'] = _get_past_consents(request.user.profile)
    study = Study.objects.first()
    studies_data = _get_active_studies_data(request.user.profile, request)
    context['study'] = study
    context['study_data'] = studies_data.get(study)
    # Studies the participant has registered for but that a researcher has not yet
    # approved. Until approved they have no consents, so they'd otherwise see
    # nothing about the study on the dashboard.
    context['pending_participations'] = StudyParticipant.objects.filter(
        participant=request.user.profile,
        researcher_approved=False,
        study__requires_approval=True,
    ).select_related('study')
    context['protected_identifiers'] = request.user.profile.protected_identifiers.order_by('id')

    # Show the tax ID form only if an active study the participant is enrolled in
    # asks for it. Tax IDs are typically collected only toward the end of a study.
    context['collect_tax_id'] = any(
        active_study.collect_tax_id for active_study in studies_data
    )
    context['tax_id'] = request.user.profile.tax_id

    card_template, card_context = _get_next_instructions_card(request, studies_data)
    if card_template and card_context:
        context['instructions_template'] = card_template
        context['instructions_context'] = card_context
    
    return render(request, 'dashboard.html', context)


@login_required
def update_protected_identifiers(request):
    """Replace the participant's protected identifiers.

    POST; login required; participants only. Uses all 'identifier' POST
    values, stripped and de-duplicated; blanks are dropped. The old set is
    deleted and the new one created in one transaction (each new identifier
    gets a new replacement), so posting no values clears the list. Redirects
    (302) to 'dashboard' with a success message. Non-POST requests redirect
    to 'dashboard' without changes, researchers to 'researcher_dashboard'.
    """
    if request.method != 'POST':
        return redirect('dashboard')

    if request.user.profile.user_type == 'researcher':
        return redirect('researcher_dashboard')

    raw_values = request.POST.getlist('identifier')
    seen = set()
    unique_values = []
    for v in raw_values:
        stripped = v.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            unique_values.append(stripped)

    profile = request.user.profile
    with transaction.atomic():
        profile.protected_identifiers.all().delete()
        ProtectedIdentifier.objects.bulk_create([
            ProtectedIdentifier(profile=profile, value=v) for v in unique_values
        ])

    messages.success(request, "Protected identifiers updated.")
    return redirect('dashboard')


@login_required
def update_tax_id(request):
    """POST; login required; participants only. Stores the 'tax_id' POST value,
    stripped, as Profile.tax_id; a missing or blank value clears it. The
    value is not validated, and is accepted whether or not a study collects
    tax IDs (the dashboard only shows the form when one does). Redirects
    (302) to 'dashboard' with a success message. Non-POST requests redirect
    to 'dashboard' without changes, researchers to 'researcher_dashboard'.
    """
    if request.method != 'POST':
        return redirect('dashboard')

    if request.user.profile.user_type == 'researcher':
        return redirect('researcher_dashboard')

    profile = request.user.profile
    profile.tax_id = request.POST.get('tax_id', '').strip()
    profile.save(update_fields=['tax_id'])

    messages.success(request, "Tax ID updated.")
    return redirect('dashboard')


@login_required
def researcher_dashboard(request):
    """GET; login required; researchers only. Renders 'researcher_dashboard.html'
    (200) listing each study the researcher belongs to, with one row per
    participant who has an unrevoked consent (consent completion counts,
    status and pseudo_id), plus total and withdrawn consent counts.
    Non-researchers are redirected (302) to 'dashboard' with an error message.
    """
    if request.user.profile.user_type != 'researcher':
        messages.error(request, "Access denied: Researcher dashboard is only for researchers.")
        return redirect('dashboard')

    studies = request.user.profile.studies.all()
    studies_data = []

    for study in studies:
        all_consents = Consent.objects.filter(study=study, revocation_date__isnull=True)
        withdrawn = Consent.objects.filter(study=study, revocation_date__isnull=False).count()

        participants = []
        participant_profiles = all_consents.values_list('participant', flat=True).distinct()
        for profile_id in participant_profiles:
            profile = Profile.objects.get(id=profile_id)
            participant_consents = all_consents.filter(participant=profile)

            study_participation = StudyParticipant.objects.filter(
                participant=profile, study=study
            ).first()

            required_consents = participant_consents.filter(is_optional=False)
            optional_consents = participant_consents.filter(is_optional=True)

            required_consents_complete = required_consents.filter(
                is_complete=True,
                data_source__status='active'
            )
            required_complete = required_consents_complete.count()
            required_total = required_consents.count()
            optional_complete = optional_consents.filter(is_complete=True).count()
            optional_total = optional_consents.count()

            status = 'Complete' if required_complete == required_total else 'Incomplete'

            participants.append({
                'id': profile.id,
                'required_complete': required_complete,
                'required_total': required_total,
                'optional_complete': optional_complete,
                'optional_total': optional_total,
                'status': status,
                'pseudo_id': study_participation.pseudo_id if study_participation else None,
            })
            
        studies_data.append({
            'study': study,
            'participants': participants,
            'total_consents': all_consents.count(),
            'withdrawn': withdrawn,
        })

    context = {'studies_data': studies_data}
    return render(request, 'researcher_dashboard.html', context)


@login_required
def participant_detail(request, study_id, participant_id):
    """Show one participant's consents and data sources in a study.

    GET; login required. study_id and participant_id are the Study and
    Profile ids. Checks run in this order:
    - a missing study or profile gives 404, for any logged-in user;
    - a non-researcher (superuser or not) is redirected (302) to 'dashboard';
    - a researcher who is neither a researcher of the study nor a superuser
      is redirected (302) to 'researcher_dashboard' with an error message.
    Otherwise renders 'users/participant_detail.html' (200). The profile does
    not have to take part in the study; then the consent list is empty.
    """
    study = get_object_or_404(Study, id=study_id)
    participant = get_object_or_404(Profile, id=participant_id)

    # Verify researcher has access to this study
    if request.user.profile.user_type != 'researcher':
        return redirect('dashboard')
    if not study.researchers.filter(user=request.user).exists() and not request.user.is_superuser:
        messages.error(request, "You don't have permission to view this participant.")
        return redirect('researcher_dashboard')

    study_participation = StudyParticipant.objects.filter(
        participant=participant, study=study
    ).first()

    consents = Consent.objects.filter(study=study, participant=participant)

    consent_info = []
    for consent in consents:
        info = {
            'source_type': consent.source_type,
            'is_optional': consent.is_optional,
            'is_complete': consent.is_complete,
            'consent_text_accepted': consent.consent_text_accepted,
            'consent_date': consent.consent_date,
        }
        if consent.data_source:
            source = consent.data_source.get_real_instance()
            info['data_source'] = {
                'name': source.name,
                'status': source.status,
                'type': source.display_type,
                'date_added': source.date_added,
            }
        else:
            info['data_source'] = None

        consent_info.append(info)

    context = {
        'study': study,
        'participant': participant,
        'consent_info': consent_info,
        'pseudo_id': study_participation.pseudo_id if study_participation else None,
    }

    return render(request, 'users/participant_detail.html', context)

@api_view(['GET'])
@authentication_classes([TokenAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def my_data_api(request):
    """Return the caller's own data from all their data sources.

    GET only; other methods get 405. Authentication, either of:
    - header ``Authorization: Token <key>`` (the key from manage_token);
    - a logged-in session (cookie).
    A missing or invalid token, or no session, gives 401 with DRF's JSON error
    body ``{"detail": ...}``. Only the caller's own Profile's data sources are
    read; researchers get their own (usually empty) data, not participants'.

    Query params, all optional:
    - data_type: only this data type; a source that doesn't offer it
      contributes nothing.
    - start_date, end_date: passed unchanged as strings to each source's
      fetch_data; their format and meaning are the source's.
    - format: 'csv' returns the rows as a CSV attachment named
      study_data.csv (200), with the header from the first row's keys; with
      no rows it returns plain text "No data available" (200) instead.
      Anything else (default 'json') returns JSON.

    A valid token whose user has no Profile gives 500 (the profile middleware
    does not see token authentication).

    JSON (200): ``{"data_count": <int>, "data_types": [<str>, ...],
    "data": [<row>, ...]}``, where data_types lists the types queried (in no
    fixed order, also when they returned no rows) and rows are the sources'
    fetch_data items.
    """
    data_type = request.GET.get('data_type')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    output_format = request.GET.get('format', 'json')

    all_data = []
    all_data_types = set()

    for source in request.user.profile.data_sources.all():
        real_source = source.get_real_instance()
        data_types = real_source.get_data_types()
        if data_type:
            data_types = [data_type] if data_type in data_types else []
        for dt in data_types:
            data = real_source.fetch_data(
                data_type=dt,
                start_date=start_date,
                end_date=end_date
            )
            all_data.extend(data)
            all_data_types.add(dt)
    
    if output_format == 'csv':
        return data_to_csv_response(all_data, "study_data.csv")
    else:
        return JsonResponse({
            'data_count': len(all_data),
            'data_types': list(all_data_types),
            'data': all_data
        })
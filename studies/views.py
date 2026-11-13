"""Study views: public pages, participant join/consent flow and researcher data API.

All routes are mounted under ``/studies/`` and act on the deployment's single
study (``Study.objects.first()``); when no study exists, the participant views
raise 404 and the API views return 404 ``{"error": "No study configured"}``.

Access groups:
- Public, no login: ``study_detail``, ``terms_of_service``,
  ``privacy_statement``, ``info_sheet`` and their ``*_version`` views.
- Login required (``login_required``; anonymous users are redirected to the
  login page with ``next``): ``join_study``, ``withdraw_from_study``,
  ``consent_workflow``, ``revoke_consent``. They need ``request.user.profile``.
- Researcher API (``api/...``): authenticated by DRF token
  (``Authorization: Token <key>``) or session. A superuser, or a user whose
  profile is in ``Study.researchers``, may call them; other authenticated
  users get 403 ``{"error": "Unauthorized"}``; unauthenticated requests get
  DRF's 401 ``{"detail": ...}``; a wrong HTTP method gets 405. Session-auth
  POSTs need a CSRF token. Each API route also answers with a trailing slash.

The researcher API for researcher-written study page HTML is
``{% include join_or_login_section %}`` only; see the ``studies_tags``
module docstring.

Data types in the API are namespaced as ``<source_type>:<data_type>`` (e.g.
``aware:battery``). Participants are identified only by
``StudyParticipant.pseudo_id`` (``participant_id``). The API only sees
consents that are complete, unrevoked and linked to an active data source,
and only the data types the consent allows.
"""

import base64
import re
from datetime import datetime
from urllib.parse import urlencode

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template import engines
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import IsAuthenticated

from data_sources.models import DataSource, DeletableWatermark
from study_server.utils import data_to_csv_response

from . import services
from .forms import ConsentAcceptanceForm, DataSourceSelectionForm
from .models import (
    Consent,
    InfoSheet,
    PrivacyNotice,
    Study,
    StudyParticipant,
    StudySourceConfiguration,
    TermsOfService,
)


def _get_study():
    study = Study.objects.first()
    if study is None:
        from django.http import Http404

        raise Http404("No study configured.")
    return study


@login_required
def join_study(request):
    """Join the study (URL name `join_study`, `/studies/join/`, login required).

    Any logged-in user with a profile may call it; the user type is not
    checked here. 404 if no study is configured.

    GET: show the join confirmation page (terms + privacy). Creates nothing,
    so a plain link to this URL is safe.
    POST: enroll; requires the form field `accept_terms`. Creates or reuses the
    StudyParticipant. The POST path is internal to the studies app: it is
    submitted only by this view's own confirmation page form. Other components
    should link here with GET.

    Returns:
        GET: the join confirmation page. POST: if researcher approval is
        required and missing, the registration-pending page (200, no
        redirect); otherwise it creates the participant's consents and
        redirects to `consent_workflow`. On failure it redirects with an error
        message: terms not accepted back to `join_study`; email domain not
        allowed or study full (for a new registration) to `home`.

    Raises:
        Http404: if no study is configured.
    """
    study = _get_study()
    profile = request.user.profile

    # GET: show the study-wide confirmation page (terms + privacy) without
    # creating any participation or consent records.
    if request.method != "POST":
        join_confirmation_content = ""
        if study.join_confirmation_html:
            template = engines["django"].from_string(study.join_confirmation_html)
            context = {
                "study": study,
                "request": request,
                "user": request.user,
                "assets": study.get_assets_dict(),
            }
            join_confirmation_content = mark_safe(template.render(context))
        return render(
            request,
            "studies/join_confirmation.html",
            {"study": study, "join_confirmation_content": join_confirmation_content},
        )

    # POST: require explicit confirmation before enrolling.
    if not request.POST.get("accept_terms"):
        messages.error(
            request,
            "You must confirm the terms of service and privacy notice to join the study.",
        )
        return redirect("join_study")

    # Backstop for restrictions configured on the study: the email-domain rule
    # is normally enforced at signup, but the restriction may have been added
    # after the account was created.
    if not study.email_allowed(request.user.email):
        allowed_domains = [
            d for d in study.allowed_domain_list() if d != "example.com"
        ]
        if len(allowed_domains) > 1:
            formatted_domains = ", ".join(allowed_domains[:-1])
            messages.error(
                request,
                f"Use an email address ending with {formatted_domains} or {allowed_domains[-1]} to join this study.",
            )
        else:
            messages.error(
                request,
                f"Use an email address ending with {allowed_domains[0]} to join this study.",
            )
        return redirect("home")

    already_registered = StudyParticipant.objects.filter(
        participant=profile, study=study
    ).exists()
    if not already_registered and study.is_full():
        messages.error(
            request, "This study is not accepting new participants at the moment."
        )
        return redirect("home")

    study_participant, _ = StudyParticipant.objects.get_or_create(
        participant=profile,
        study=study,
    )

    # Participants must be approved by a researcher before they can consent or
    # collect data. Registration only records the StudyParticipant; consents are
    # created once the participant is approved (here or in consent_workflow).
    if study.requires_approval and not study_participant.researcher_approved:
        return _render_registration_pending(request, study)

    _create_consents_for_participant(study, profile, study_participant)

    messages.info(
        request,
        f"You have started the enrollment process for '{study.title}'. Please complete the required steps.",
    )
    return redirect("consent_workflow")


def _create_consents_for_participant(study, profile, study_participant):
    """Create the study's required/optional consents for an approved participant.

    Idempotent: an active consent for a source configuration is never
    duplicated, so this is safe to call whenever the participant (re-)enters the
    flow. Revoked consents are ignored so a participant who withdrew can re-join
    and get a fresh consent.
    """

    def ensure_consent(source_configuration, is_optional):
        already = Consent.objects.filter(
            participant=profile,
            study=study,
            source_configuration=source_configuration,
            revocation_date__isnull=True,
        ).exists()
        if already:
            return
        consent = Consent(
            participant=profile,
            study=study,
            source_configuration=source_configuration,
            source_type=source_configuration.source_type,
            is_optional=is_optional,
            study_participant=study_participant,
        )
        # Consent for this source was given on the join page, so the
        # separate consent form is skipped.
        if not source_configuration.show_separate_consent_page:
            consent.accept_configuration()
        consent.save()

    for source_configuration in study.source_config_entries.filter(status="required"):
        ensure_consent(source_configuration, is_optional=False)

    for source_configuration in study.source_config_entries.filter(status="optional"):
        ensure_consent(source_configuration, is_optional=True)


def _render_registration_pending(request, study):
    """Render the 'registration noted, awaiting approval' page for a participant."""
    pending_content = ""
    if study.registration_pending_html:
        template = engines["django"].from_string(study.registration_pending_html)
        context = {
            "study": study,
            "request": request,
            "user": request.user,
            "assets": study.get_assets_dict(),
        }
        pending_content = mark_safe(template.render(context))
    return render(
        request,
        "studies/registration_pending.html",
        {"study": study, "pending_content": pending_content},
    )


@login_required
def withdraw_from_study(request):
    """Withdraw from the study (URL name `withdraw_from_study`, login required).

    GET: show the withdrawal confirmation page. Changes nothing, so a plain
    link to this URL is safe.
    POST: revoke all of the participant's active consents in the study, then
    redirect to `dashboard`.

    The POST path is internal to the studies app: it is submitted only by this
    view's own confirmation page form. Other components should link here with
    GET.
    """
    study = _get_study()
    profile = request.user.profile

    if request.method == "POST":
        consents = Consent.objects.filter(
            participant=profile, study=study, revocation_date__isnull=True
        )
        for consent in consents:
            consent.data_source = None
            consent.revocation_date = timezone.now()
            consent.is_complete = False
            consent.save()
        messages.success(
            request, f"You have successfully withdrawn from the study '{study.title}'."
        )
        return redirect("dashboard")

    return render(request, "studies/withdraw.html", {"study": study})


@login_required
def revoke_consent(request, consent_id):
    """Revoke one optional consent (URL name `revoke_consent`, login required).

    URL: `/studies/revoke/<consent_id>/`. A mandatory consent cannot be
    revoked here: any method redirects to `withdraw_from_study` with a
    message.

    GET: show the revoke confirmation page; changes nothing.
    POST: revoke the consent (unlink its data source), create a fresh empty
    optional consent for the same source so it can be given again later, and
    redirect to `dashboard`.

    Args:
        consent_id: id of the consent to revoke.

    Raises:
        Http404: unless the consent belongs to the logged-in user and is not
            already revoked.
    """
    profile = request.user.profile
    consent = get_object_or_404(
        Consent, id=consent_id, participant=profile, revocation_date__isnull=True
    )
    study = consent.study

    if not consent.is_optional:
        messages.info(
            request,
            "This consent is mandatory for the study. To withdraw, please use the withdrawal page.",
        )
        return redirect("withdraw_from_study")

    if request.method == "POST":
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
        messages.success(
            request,
            f"You have revoked consent for {consent.source_type} in '{study.title}'.",
        )
        return redirect("dashboard")

    return render(
        request, "studies/revoke_consent.html", {"study": study, "consent": consent}
    )


def study_detail(request):
    """Show the public study page (URL name `study_detail`, `/studies/`).

    The page HTML is researcher-written (`Study.study_page_html`). Its only
    supported interface is `{% include join_or_login_section %}`; see the
    `studies_tags` module docstring. When the field is empty, the default
    `studies/study_page_default.html` (title, description, join section) is
    shown.

    GET; no login required and read-only (other methods are not rejected but
    behave the same). Renders the study page HTML, with `user_in_study`
    telling the page whether the user has an active consent.
    """
    study = _get_study()
    user_in_study = False
    if request.user.is_authenticated and hasattr(request.user, "profile"):
        user_in_study = Consent.objects.filter(
            participant=request.user.profile, study=study, revocation_date__isnull=True
        ).exists()

    if study.study_page_html:
        template = engines["django"].from_string(study.study_page_html)
    else:
        template = engines["django"].get_template("studies/study_page_default.html")

    context = {
        "study": study,
        "request": request,
        "user": request.user,
        "user_in_study": user_in_study,
        "join_or_login_section": "studies/study_page_components/join_or_login_section.html",
        "assets": study.get_assets_dict(),
    }
    return render(
        request,
        "studies/study_detail_wrapper.html",
        {"study_page_content": template.render(context)},
    )


def _render_versioned_html(request, html_content):
    """Render researcher-configured HTML through the Django template engine,
    wrapped in the standard study page layout."""
    study = Study.objects.first()
    template = engines["django"].from_string(html_content)
    context = {
        "study": study,
        "request": request,
        "user": request.user,
        "assets": study.get_assets_dict() if study else {},
    }
    return render(
        request,
        "studies/study_detail_wrapper.html",
        {"study_page_content": mark_safe(template.render(context))},
    )


def terms_of_service(request):
    """Show the current terms of service (URL name `terms_of_service`, `/studies/terms/`).

    Public, GET, read-only. Renders the latest TermsOfService version as a
    template in the study page layout; falls back to the static
    `terms_of_service.html` template when no version or an empty one exists.
    """
    version = TermsOfService.current()
    if version and version.text:
        return _render_versioned_html(request, version.text)
    return render(request, 'terms_of_service.html')

def privacy_statement(request):
    """Show the current privacy notice (URL name `privacy_statement`, `/studies/privacy/`).

    Public, GET, read-only. Renders the latest PrivacyNotice version as a
    template in the study page layout; falls back to the static
    `privacy_statement.html` template when no version or an empty one exists.
    """
    version = PrivacyNotice.current()
    if version and version.text:
        return _render_versioned_html(request, version.text)
    return render(request, 'privacy_statement.html')


def terms_of_service_version(request, pk):
    """Show one terms of service version by pk (URL name `terms_of_service_version`).

    URL: `/studies/terms/version/<pk>/`. Public, GET, read-only; any past or
    current version can be viewed.

    Args:
        pk: primary key of the terms of service version.

    Raises:
        Http404: if the pk does not exist.
    """
    version = get_object_or_404(TermsOfService, pk=pk)
    return _render_versioned_html(request, version.text)


def privacy_statement_version(request, pk):
    """Show one privacy notice version by pk (URL name `privacy_statement_version`).

    URL: `/studies/privacy/version/<pk>/`. Public, GET, read-only; any past or
    current version can be viewed.

    Args:
        pk: primary key of the privacy notice version.

    Raises:
        Http404: if the pk does not exist.
    """
    version = get_object_or_404(PrivacyNotice, pk=pk)
    return _render_versioned_html(request, version.text)


def info_sheet(request):
    """Show the current information sheet (URL name `info_sheet`, `/studies/info-sheet/`).

    Public, GET, read-only. Falls back to the placeholder template
    `studies/info_sheet.html` when no version or an empty one exists.
    """
    version = InfoSheet.current()
    if version and version.text:
        return _render_versioned_html(request, version.text)
    return render(request, "studies/info_sheet.html")


def info_sheet_version(request, pk):
    """Show one information sheet version by pk (URL name `info_sheet_version`).

    URL: `/studies/info-sheet/version/<pk>/`. Public, GET, read-only.

    Args:
        pk: primary key of the information sheet version.

    Raises:
        Http404: if the pk does not exist.
    """
    version = get_object_or_404(InfoSheet, pk=pk)
    return _render_versioned_html(request, version.text)


def _get_next_consent(profile, study, consent_id=None):
    if consent_id:
        return get_object_or_404(
            Consent,
            id=consent_id,
            participant=profile,
            study=study,
            revocation_date__isnull=True,
        )
    return Consent.objects.filter(
        participant=profile,
        study=study,
        is_complete=False,
        is_optional=False,
        revocation_date__isnull=True,
    ).first()


def _consent_checkbox_view(request, consent, study):
    source_type = (
        consent.source_configuration.source_type
        if consent.source_configuration
        else consent.source_type
    )
    html_template = services.get_consent_template(
        study, source_type, consent.source_configuration
    )
    template = engines["django"].from_string(html_template)
    source_configuration = consent.source_configuration
    if source_configuration:
        model_cls = source_configuration.get_data_source_class()
        if model_cls:
            source_type_label = model_cls.display_type_for_configuration(
                source_configuration.configuration
            )
        else:
            source_type_label = source_configuration.source_type.replace(
                "_", " "
            ).title()
    else:
        source_type_label = consent.source_type

    if request.method == "POST":
        form = ConsentAcceptanceForm(request.POST)
        if form.is_valid():
            consent.accept_configuration()
            if consent.data_source:
                consent.complete_with_source(consent.data_source)
            else:
                consent.save()
            return redirect(f"{reverse('consent_workflow')}?consent_id={consent.id}")
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    else:
        form = ConsentAcceptanceForm()

    checkbox_template = get_template("studies/consent_checkbox_form.html")
    consent_form_html = checkbox_template.render({"form": form}, request)

    context = {
        "consent": consent,
        "study": study,
        "consent_form": mark_safe(consent_form_html),
        "request": request,
        "source_type_label": source_type_label,
    }
    rendered = template.render(context)
    return render(
        request,
        "studies/consent_wrapper.html",
        {"content": rendered, "scroll_to": "consent-form"},
    )


def _create_data_source_flow(consent):
    source_configuration = consent.source_configuration
    if not source_configuration:
        from django.http import Http404

        raise Http404("Missing source configuration for consent.")
    base_url = reverse("add_data_source", args=[source_configuration.source_type])
    query_params = urlencode(
        {
            "consent_id": consent.id,
            "source_configuration_id": source_configuration.id,
        }
    )
    return redirect(f"{base_url}?{query_params}")


def _get_existing_sources_for_consent(profile, consent):
    model_cls = DataSource.get_class_for_type(consent.source_type)
    if not model_cls:
        return []

    sources = profile.data_sources.filter(
        polymorphic_ctype__model=model_cls.__name__.lower(),
    )
    # Match against the consent's snapshotted configuration (what the participant
    # agreed to). matches_configuration returns True for empty config, so this
    # safely disambiguates multiple sources of the same type (e.g. niimport variants).
    return [
        source
        for source in sources
        if source.matches_configuration(consent.configuration or {})
    ]


def _select_data_source_view(request, consent, profile, study):
    source_type = (
        consent.source_configuration.source_type
        if consent.source_configuration
        else consent.source_type
    )
    html_template = services.get_consent_template(
        study, source_type, consent.source_configuration
    )
    template = engines["django"].from_string(html_template)
    source_configuration = consent.source_configuration
    if source_configuration:
        model_cls = source_configuration.get_data_source_class()
        if model_cls:
            source_type_label = model_cls.display_type_for_configuration(
                source_configuration.configuration
            )
        else:
            source_type_label = source_configuration.source_type.replace(
                "_", " "
            ).title()
    else:
        source_type_label = source_type

    available_sources = _get_existing_sources_for_consent(profile, consent)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "select":
            form = DataSourceSelectionForm(
                request.POST, available_sources=available_sources
            )
            if form.is_valid() and form.cleaned_data["source_id"]:
                source = profile.data_sources.filter(
                    id=form.cleaned_data["source_id"]
                ).first()
                # Re-validate against the matched set: a POSTed source_id must
                # actually match this consent's configuration, not just belong to
                # the participant.
                if source and any(
                    source.id == candidate.id for candidate in available_sources
                ):
                    consent.complete_with_source(source)
                    return redirect("consent_workflow")
                messages.error(
                    request,
                    "That data source does not match what this consent requires.",
                )
            else:
                for errors in form.errors.values():
                    for error in errors:
                        messages.error(request, error)
        elif action == "create":
            if not consent.source_configuration:
                from django.http import Http404

                raise Http404("Missing source configuration for consent.")
            base_url = reverse(
                "add_data_source", args=[consent.source_configuration.source_type]
            )
            query_params = urlencode(
                {
                    "consent_id": consent.id,
                    "source_configuration_id": consent.source_configuration.id,
                }
            )
            return redirect(f"{base_url}?{query_params}")
    else:
        form = DataSourceSelectionForm(available_sources=available_sources)

    source_form_template = get_template("studies/source_selection_form.html")
    source_form_html = source_form_template.render(
        {"form": form, "consent": consent}, request
    )

    context = {
        "consent": consent,
        "study": study,
        "consent_form": source_form_html,
        "source_type_label": source_type_label,
    }
    rendered = template.render(context)
    return render(
        request,
        "studies/consent_wrapper.html",
        {"content": rendered, "scroll_to": "consent-form"},
    )


@login_required
def consent_workflow(request):
    """Step through the participant's consents (URL name `consent_workflow`, login required).

    URL: `/studies/consent/`, GET or POST. A user who has not joined, or is
    awaiting required researcher approval, gets the registration-pending page
    (200). Otherwise it makes sure the participant's consents exist and
    handles one consent: the one named by the optional query parameter
    `consent_id` (404 unless it is the user's own unrevoked consent in this
    study), else the first incomplete required consent. With none left it
    redirects to `dashboard`.

    For that consent: if its text is not yet accepted, GET shows the consent
    page and POST (form field `accept_consent`) accepts it and redirects back
    here with `consent_id`. Once accepted: if the user has no matching data
    source, it redirects to the data_sources `add_data_source` view with
    `consent_id` and `source_configuration_id`; otherwise GET shows a picker
    and POST takes `action=select` with `source_id` (completes the consent,
    redirects here) or `action=create` (redirects to `add_data_source`).
    Optional consents are reached only through `consent_id`.

    Raises:
        Http404: if no study is configured, or `consent_id` is not the user's
            own unrevoked consent in this study.
    """
    study = _get_study()
    profile = request.user.profile

    # Approval gate: an unapproved (or not-yet-registered) participant cannot
    # reach the consent flow, even by navigating directly to this URL.
    study_participant = StudyParticipant.objects.filter(
        participant=profile, study=study
    ).first()
    if not study_participant or (
        study.requires_approval and not study_participant.researcher_approved
    ):
        return _render_registration_pending(request, study)

    # An approved participant may have registered before approval, so ensure
    # their consents exist before continuing.
    _create_consents_for_participant(study, profile, study_participant)

    consent_id = request.GET.get("consent_id")
    consent = _get_next_consent(profile, study, consent_id)

    if not consent:
        messages.success(request, f"All required sources set up for '{study.title}'")
        return redirect("dashboard")

    # step 1: show the consent checkbox
    if not consent.consent_text_accepted:
        return _consent_checkbox_view(request, consent, study)

    # step 2: select or create data source
    available_sources = _get_existing_sources_for_consent(profile, consent)
    if not available_sources:
        return _create_data_source_flow(consent)
    else:
        return _select_data_source_view(request, consent, profile, study)


def _clean_row(row):
    for k, v in row.items():
        if isinstance(v, bytes):
            try:
                row[k] = v.decode("utf-8")
            except Exception:
                row[k] = base64.b64encode(v).decode("ascii")
    return row


def _parse_date(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d") if date_str else None


def _make_timezone_aware(dt):
    if dt is not None and timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt


def _split_namespaced_type(value):
    slug, sep, bare = (value or '').partition(':')
    if not sep:
        return None, None
    return slug, bare


def _allowed_data_types(consent):
    """Return the data types the participant consented to share.

    ``requested_data_types`` is a comma-separated snapshot taken at consent time;
    an empty value means no restriction (all of the source's types).

    Returns:
        The set of allowed data types, or None when unrestricted.
    """
    raw = (consent.requested_data_types or "").strip()
    if not raw:
        return None
    return {t.strip() for t in raw.split(",") if t.strip()}


@api_view(["GET"])
@authentication_classes([TokenAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def study_data_api(request):
    """List data types or fetch one participant's data (URL name `study_data_api`).

    URL: `/studies/api/data`, GET only. Researcher API auth (module docstring).

    Without `data_type`: lists the types, sorted, as the union over all
    participants of the types they consented to.

    With `data_type` (namespaced `<source_type>:<data_type>`), query params:
    - `participant_id` (required): a participant's pseudo_id.
    - `timestamp` (Unix ms, default 0, >= 0): fetch rows after this cursor.
      The cursor is raised to the start of the consent's `data_start` day
      (server timezone); rows after the end of the `data_end` day are dropped.
    - `limit` (default 1000, 1..10000): maximum rows fetched from the source.
    - `format`: `csv` returns a CSV download (`study_data.csv`); anything
      else returns JSON.
    - `advance` (`true`/`1`/`yes`): move the download watermark (see below).
    Each row gets `data_type`, `source_type`, `origin` (the
    consent configuration's `niimport_source_type`, else null) and
    `participant_id`; bytes values become UTF-8 text or base64; the
    participant's protected identifiers are replaced (case-insensitive) in
    every string value.

    Watermark (`advance`): the watermark `downloaded_through` for this data
    source and data type records how far data has been downloaded without
    gaps. If `timestamp` is at most one ms past the watermark (or, with no
    watermark yet, at most the consent's start), the watermark moves to the
    largest returned row timestamp. Otherwise the request fails with 400
    `{"error": "gap: ...", "downloaded_through": <ms or null>}` and returns
    no data. `mark_data_deletable` relies on this watermark.

    Returns:
        Without `data_type`:
        `{"study": <title>, "data_types": [<"source_type:data_type">, ...]}`.
        With `data_type`, JSON `{"study", "data_type", "participant_id",
        "count", "data": [rows]}`, or a CSV download for `format=csv`.
        Errors are JSON `{"error": ...}`: 400 for a non-namespaced
        `data_type`, a missing `participant_id`, a non-integer or negative
        `timestamp`, a bad `limit`, or a gap with `advance`; 404 when no study
        exists or no active consent of that participant covers the data type.
    """
    study = Study.objects.first()
    if study is None:
        return JsonResponse({"error": "No study configured"}, status=404)

    if not request.user.is_superuser:
        if not study.researchers.filter(user=request.user).exists():
            return JsonResponse({"error": "Unauthorized"}, status=403)

    data_type = request.GET.get("data_type")

    active_consents = (
        Consent.objects.filter(
            study=study,
            is_complete=True,
            revocation_date__isnull=True,
            data_source__status="active",
        )
        .select_related("data_source", "study_participant")
        .order_by("id")
    )

    # --- List branch: no data_type given ---
    if not data_type:
        all_data_types = set()
        for consent in active_consents:
            if not consent.data_source:
                continue
            source = consent.data_source.get_real_instance()
            available = set(source.get_data_types())
            allowed = _allowed_data_types(consent)
            if allowed is not None:
                available &= allowed
            for dt in available:
                all_data_types.add(f"{source.SOURCE_TYPE}:{dt}")
        return JsonResponse(
            {
                "study": study.title,
                "data_types": sorted(all_data_types),
            }
        )

    # --- Fetch branch: data_type given ---
    slug, bare = _split_namespaced_type(data_type)
    if slug is None:
        return JsonResponse(
            {"error": "data_type must be namespaced as <source_type>:<data_type>"},
            status=400,
        )

    participant_id = request.GET.get("participant_id")
    if not participant_id:
        return JsonResponse({"error": "participant_id is required"}, status=400)

    try:
        timestamp = int(request.GET.get("timestamp", 0))
        limit = int(request.GET.get("limit", 1000))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid query parameters"}, status=400)

    if timestamp < 0:
        return JsonResponse({"error": "Invalid query parameters"}, status=400)
    if limit < 1:
        return JsonResponse({"error": "Invalid limit or offset"}, status=400)
    if limit > 10000:
        return JsonResponse({"error": "Maximum limit is 10000"}, status=400)

    advance_raw = request.GET.get("advance", "").strip().lower()
    advance = advance_raw in ("true", "1", "yes")
    output_format = request.GET.get("format", "json")

    # Resolve single matching consent
    matched_consent = None
    matched_source = None
    for consent in active_consents:
        if not consent.data_source:
            continue
        if not consent.study_participant:
            continue
        if str(consent.study_participant.pseudo_id) != participant_id:
            continue
        source = consent.data_source.get_real_instance()
        if source.SOURCE_TYPE != slug:
            continue
        if bare not in source.get_data_types():
            continue
        allowed = _allowed_data_types(consent)
        if allowed is not None and bare not in allowed:
            continue
        matched_consent = consent
        matched_source = source
        break

    if matched_consent is None:
        return JsonResponse({"error": "No matching data source"}, status=404)

    consent = matched_consent
    source = matched_source

    # Compute consent window in Unix ms
    if consent.data_start:
        data_start_ms = int(
            _make_timezone_aware(
                datetime.combine(consent.data_start, datetime.min.time())
            ).timestamp() * 1000
        )
    else:
        data_start_ms = 0

    if consent.data_end:
        data_end_ms = int(
            _make_timezone_aware(
                datetime.combine(consent.data_end, datetime.max.time())
            ).timestamp() * 1000
        )
    else:
        data_end_ms = None

    effective_cursor = max(int(timestamp), data_start_ms)

    rows = source.fetch_data(bare, timestamp=effective_cursor, limit=limit)
    if not isinstance(rows, list):
        rows = []

    # Post-cap by data_end_ms
    if data_end_ms is not None:
        rows = [
            row for row in rows
            if not (row.get("timestamp") is not None and int(row["timestamp"]) > data_end_ms)
        ]

    # Identifiers the participant wants replaced before researchers see the data
    profile = consent.study_participant.participant
    # Longest first, so "name@example.com" is replaced before "name" can split it
    identifiers = sorted(
        profile.protected_identifiers.values_list("value", "replacement"),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
    identifier_patterns = [
        (re.compile(re.escape(identifier), re.IGNORECASE), replacement)
        for identifier, replacement in identifiers
    ]

    # Enrich rows
    for row in rows:
        row["data_type"] = data_type
        row["source_type"] = consent.source_type
        row["origin"] = (consent.configuration or {}).get("niimport_source_type")
        row["participant_id"] = participant_id
        _clean_row(row)
        for key, value in row.items():
            if not isinstance(value, str):
                continue
            for pattern, replacement in identifier_patterns:
                value = pattern.sub(lambda _match: replacement, value)
            row[key] = value

    # Advance watermark handling
    if advance:
        wm, _ = DeletableWatermark.objects.get_or_create(
            data_source=consent.data_source, data_type=bare
        )
        D = wm.downloaded_through
        floor = D if D is not None else (data_start_ms - 1)

        if int(timestamp) < floor + 2:
            # Contiguous: safe to advance
            max_ts = max(
                (int(r["timestamp"]) for r in rows if r.get("timestamp") is not None),
                default=None,
            )
            if max_ts is not None:
                with transaction.atomic():
                    wm_l = DeletableWatermark.objects.select_for_update().get(pk=wm.pk)
                    new_val = max_ts if wm_l.downloaded_through is None else max(wm_l.downloaded_through, max_ts)
                    if wm_l.downloaded_through != new_val:
                        wm_l.downloaded_through = new_val
                        wm_l.save(update_fields=["downloaded_through", "updated_at"])
        else:
            # Gap detected: refuse
            downloaded_desc = (
                "no data has been downloaded yet"
                if D is None
                else f"data has only been downloaded through timestamp {D}"
            )
            return JsonResponse(
                {
                    "error": (
                        f"gap: requested start timestamp {timestamp} is ahead of "
                        f"the download cursor ({downloaded_desc} for this "
                        f"participant/data type). Advancing would mark skipped "
                        f"rows as downloaded. Start at or before the cursor, or "
                        f"request without advance=true."
                    ),
                    "downloaded_through": D,
                },
                status=400,
            )

    if output_format == "csv":
        return data_to_csv_response(rows, "study_data.csv")
    return JsonResponse(
        {
            "study": study.title,
            "data_type": data_type,
            "participant_id": participant_id,
            "count": len(rows),
            "data": rows,
        }
    )


@api_view(["GET"])
@authentication_classes([TokenAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def study_participants_api(request):
    """List participant ids with shareable data (URL name `study_participants_api`).

    URL: `/studies/api/participants`, GET only. Researcher API auth (module
    docstring). Optional query param `data_type` (`<source_type>:<data_type>`)
    limits the list to participants whose active consent covers that type.

    Returns:
        `{"study": <title>, "participants": [<pseudo_id>, ...]}`, sorted
        and unique. Errors: 400 for a non-namespaced `data_type`; 404 when no
        study exists.
    """
    study = Study.objects.first()
    if study is None:
        return JsonResponse({"error": "No study configured"}, status=404)

    if not request.user.is_superuser:
        if not study.researchers.filter(user=request.user).exists():
            return JsonResponse({"error": "Unauthorized"}, status=403)

    data_type = request.GET.get("data_type")
    slug = None
    bare = None
    if data_type:
        slug, bare = _split_namespaced_type(data_type)
        if slug is None:
            return JsonResponse(
                {"error": "data_type must be namespaced as <source_type>:<data_type>"},
                status=400,
            )

    active_consents = (
        Consent.objects.filter(
            study=study,
            is_complete=True,
            revocation_date__isnull=True,
            data_source__status="active",
        )
        .select_related("data_source", "study_participant")
    )

    participant_ids = []
    for consent in active_consents:
        if not consent.study_participant:
            continue
        if data_type:
            if not consent.data_source:
                continue
            source = consent.data_source.get_real_instance()
            if source.SOURCE_TYPE != slug:
                continue
            if bare not in source.get_data_types():
                continue
            allowed = _allowed_data_types(consent)
            if allowed is not None and bare not in allowed:
                continue
        participant_ids.append(str(consent.study_participant.pseudo_id))

    return JsonResponse({"study": study.title, "participants": sorted(set(participant_ids))})


@api_view(["POST"])
@authentication_classes([TokenAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def mark_data_deletable(request):
    """Mark downloaded data as deletable at its source (URL name `mark_data_deletable`).

    URL: `/studies/api/data/mark-deletable`, POST only. Researcher API auth
    (module docstring). Body (form or JSON):
    - `data_type` (required): `<source_type>:<data_type>`.
    - `through` (required): integer cutoff, Unix ms.

    Applies to every participant whose active consent covers the data type.
    A source is marked (`source.mark_deletable(data_type, through=...)` and
    the watermark's `deletable_through` set to `through`) only if the source
    supports deletion, its data has been downloaded through `through` (the
    `downloaded_through` watermark set by `study_data_api` with `advance`),
    and it has data newer than `through`, so the newest row is always kept.

    Returns:
        200 `{"study", "data_type", "marked_count", "results": [...]}`;
        each result has `participant_id`, `source_type`, `marked`, and either
        `deletable_through` or `reason` (`source does not support deletion`,
        `no downloaded data`, `data not downloaded through cutoff`,
        `no data newer than cutoff`, `source error` with `detail`).
        Errors: 400 for a missing or non-namespaced `data_type`, or a missing
        or non-integer `through`; 404 when no study exists.
    """
    study = Study.objects.first()
    if study is None:
        return JsonResponse({"error": "No study configured"}, status=404)

    if not request.user.is_superuser:
        if not study.researchers.filter(user=request.user).exists():
            return JsonResponse({"error": "Unauthorized"}, status=403)

    data_type = request.data.get("data_type", "").strip()
    if not data_type:
        return JsonResponse({"error": "data_type is required"}, status=400)

    slug, bare = _split_namespaced_type(data_type)
    if slug is None:
        return JsonResponse(
            {"error": "data_type must be namespaced as <source_type>:<data_type>"},
            status=400,
        )

    through_raw = request.data.get('through', None)
    if through_raw is None or through_raw == '':
        return JsonResponse({'error': 'through is required'}, status=400)
    try:
        through = int(through_raw)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'through must be an integer (Unix ms)'}, status=400)

    active_consents = (
        Consent.objects.filter(
            study=study,
            is_complete=True,
            revocation_date__isnull=True,
            data_source__status="active",
        )
        .select_related("data_source", "study_participant")
        .order_by("id")
    )

    results = []
    for consent in active_consents:
        if not consent.data_source:
            continue

        source = consent.data_source.get_real_instance()

        if source.SOURCE_TYPE != slug:
            continue

        if bare not in source.get_data_types():
            continue

        allowed = _allowed_data_types(consent)
        if allowed is not None and bare not in allowed:
            continue

        participant_id = (
            str(consent.study_participant.pseudo_id)
            if consent.study_participant
            else None
        )
        source_type = consent.source_type

        if not source.supports_deletion():
            results.append(
                {
                    "participant_id": participant_id,
                    "source_type": source_type,
                    "marked": False,
                    "reason": "source does not support deletion",
                }
            )
            continue

        wm = DeletableWatermark.objects.filter(
            data_source=consent.data_source, data_type=bare
        ).first()
        D = wm.downloaded_through if wm else None

        if D is None:
            results.append(
                {
                    "participant_id": participant_id,
                    "source_type": source_type,
                    "marked": False,
                    "reason": "no downloaded data",
                }
            )
            continue

        if D < through:
            results.append(
                {
                    "participant_id": participant_id,
                    "source_type": source_type,
                    "marked": False,
                    "reason": "data not downloaded through cutoff",
                }
            )
            continue

        M = source.latest_timestamp(bare)
        if M is None or M <= through:
            results.append(
                {
                    "participant_id": participant_id,
                    "source_type": source_type,
                    "marked": False,
                    "reason": "no data newer than cutoff",
                }
            )
            continue

        try:
            source.mark_deletable(bare, through=through)
        except Exception as e:
            results.append(
                {
                    "participant_id": participant_id,
                    "source_type": source_type,
                    "marked": False,
                    "reason": "source error",
                    "detail": str(e),
                }
            )
            continue

        with transaction.atomic():
            wm_locked = DeletableWatermark.objects.select_for_update().get(pk=wm.pk)
            wm_locked.deletable_through = through
            wm_locked.save(update_fields=["deletable_through", "updated_at"])

        results.append(
            {
                "participant_id": participant_id,
                "source_type": source_type,
                "marked": True,
                "deletable_through": through,
            }
        )

    marked_count = sum(1 for r in results if r.get("marked"))
    return JsonResponse(
        {
            "study": study.title,
            "data_type": data_type,
            "results": results,
            "marked_count": marked_count,
        }
    )


@api_view(["POST"])
@authentication_classes([TokenAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def unmark_data_deletable(request):
    """Cancel all pending deletable marks (URL name `unmark_data_deletable`).

    URL: `/studies/api/data/unmark-deletable`, POST only, no body. Researcher
    API auth (module docstring). For every active consent whose source
    supports deletion, calls `source.unmark_deletable()` and clears
    `deletable_through` on all of that source's watermarks (all data types).
    `downloaded_through` is kept.

    Returns:
        200 `{"study", "unmarked_count", "results": [...]}`; each result
        has `participant_id`, `source_type`, `unmarked`, and either
        `watermarks_cleared` (count) or `reason: "source error"` with
        `detail`. 404 when no study exists.
    """
    study = Study.objects.first()
    if study is None:
        return JsonResponse({"error": "No study configured"}, status=404)

    if not request.user.is_superuser:
        if not study.researchers.filter(user=request.user).exists():
            return JsonResponse({"error": "Unauthorized"}, status=403)

    active_consents = (
        Consent.objects.filter(
            study=study,
            is_complete=True,
            revocation_date__isnull=True,
            data_source__status="active",
        )
        .select_related("data_source", "study_participant")
        .order_by("id")
    )

    results = []
    for consent in active_consents:
        if not consent.data_source:
            continue

        source = consent.data_source.get_real_instance()

        if not source.supports_deletion():
            continue

        participant_id = (
            str(consent.study_participant.pseudo_id)
            if consent.study_participant
            else None
        )
        source_type = consent.source_type

        try:
            source.unmark_deletable()
        except Exception as e:
            results.append(
                {
                    "participant_id": participant_id,
                    "source_type": source_type,
                    "unmarked": False,
                    "reason": "source error",
                    "detail": str(e),
                }
            )
            continue

        with transaction.atomic():
            cleared = (
                DeletableWatermark.objects.select_for_update()
                .filter(
                    data_source=consent.data_source,
                    deletable_through__isnull=False,
                )
                .update(deletable_through=None, updated_at=timezone.now())
            )

        results.append(
            {
                "participant_id": participant_id,
                "source_type": source_type,
                "unmarked": True,
                "watermarks_cleared": cleared,
            }
        )

    unmarked_count = sum(1 for r in results if r.get("unmarked"))
    return JsonResponse(
        {
            "study": study.title,
            "results": results,
            "unmarked_count": unmarked_count,
        }
    )

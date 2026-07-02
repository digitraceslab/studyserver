import base64
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
from .models import Consent, Study, StudyParticipant, StudySourceConfiguration


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
        allowed_domains = study.allowed_domain_list()
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
        return render_registration_pending(request, study)

    create_consents_for_participant(study, profile, study_participant)

    messages.info(
        request,
        f"You have started the enrollment process for '{study.title}'. Please complete the required steps.",
    )
    return redirect("consent_workflow")


def create_consents_for_participant(study, profile, study_participant):
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
        Consent.objects.create(
            participant=profile,
            study=study,
            source_configuration=source_configuration,
            source_type=source_configuration.source_type,
            is_optional=is_optional,
            study_participant=study_participant,
        )

    for source_configuration in study.source_config_entries.filter(status="required"):
        ensure_consent(source_configuration, is_optional=False)

    for source_configuration in study.source_config_entries.filter(status="optional"):
        ensure_consent(source_configuration, is_optional=True)


def render_registration_pending(request, study):
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
    study = get_study()
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
    study = get_study()
    html_content = study.study_page_html
    if not html_content:
        html_content = services.get_study_page_html(study.raw_content_base_url)
    user_in_study = False
    if request.user.is_authenticated and hasattr(request.user, "profile"):
        user_in_study = Consent.objects.filter(
            participant=request.user.profile, study=study, revocation_date__isnull=True
        ).exists()

    template = engines["django"].from_string(html_content)

    context = {
        "study": study,
        "request": request,
        "user": request.user,
        "user_in_study": user_in_study,
        "config_repository": study.raw_content_base_url,
        "join_or_login_section": "studies/study_page_components/join_or_login_section.html",
        "assets": study.get_assets_dict(),
    }
    return render(
        request,
        "studies/study_detail_wrapper.html",
        {"study_page_content": template.render(context)},
    )


def get_next_consent(profile, study, consent_id=None):
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


def consent_checkbox_view(request, consent, study):
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


def create_data_source_flow(consent):
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
    return [
        source
        for source in sources
        if source._matches_configuration(consent.configuration or {})
    ]


def select_data_source_view(request, consent, profile, study):
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

    available_sources = get_existing_sources_for_consent(profile, consent)

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
    study = get_study()
    profile = request.user.profile

    # Approval gate: an unapproved (or not-yet-registered) participant cannot
    # reach the consent flow, even by navigating directly to this URL.
    study_participant = StudyParticipant.objects.filter(
        participant=profile, study=study
    ).first()
    if not study_participant or (
        study.requires_approval and not study_participant.researcher_approved
    ):
        return render_registration_pending(request, study)

    # An approved participant may have registered before approval, so ensure
    # their consents exist before continuing.
    create_consents_for_participant(study, profile, study_participant)

    consent_id = request.GET.get("consent_id")
    consent = get_next_consent(profile, study, consent_id)

    if not consent:
        messages.success(request, f"All required sources set up for '{study.title}'")
        return redirect("dashboard")

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
    """Data types the participant consented to share, or None when unrestricted.

    ``requested_data_types`` is a comma-separated snapshot taken at consent time;
    an empty value means no restriction (all of the source's types).
    """
    raw = (consent.requested_data_types or "").strip()
    if not raw:
        return None
    return {t.strip() for t in raw.split(",") if t.strip()}


@api_view(["GET"])
@authentication_classes([TokenAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def study_data_api(request):
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

    # Enrich rows
    for row in rows:
        row["data_type"] = data_type
        row["source_type"] = consent.source_type
        row["origin"] = (consent.configuration or {}).get("niimport_source_type")
        row["participant_id"] = participant_id
        _clean_row(row)

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
            return JsonResponse(
                {
                    "error": "gap: requested timestamp is ahead of the download cursor",
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

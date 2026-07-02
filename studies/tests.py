import uuid
from datetime import datetime, timedelta, date
from unittest.mock import patch
from django.conf import settings
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth.models import User
from django.utils import timezone
from django.http import Http404

from users.models import Profile
from data_sources.models.aware import AwareDataSource
from data_sources.models.jsonurl import JsonUrlDataSource
from data_sources.models.niimport import NiimportDataSource
from .models import Study, Consent, StudyParticipant, StudySourceConfiguration
from .views import get_next_consent, _make_timezone_aware


def expected_ms(d):
    """Convert a date (or datetime.date) to Unix milliseconds using the same
    conversion the view uses for consent.data_start / consent.data_end."""
    return int(_make_timezone_aware(datetime.combine(d, datetime.min.time())).timestamp() * 1000)


def get_source_config(study, source_type):
    """Return one StudySourceConfiguration of the given type for a study.

    Uses ``.filter(...).first()`` rather than ``.get()`` because a study may have
    several configurations of the same source type (e.g. niimport variants);
    ``.get()`` would raise MultipleObjectsReturned.
    """
    return study.source_config_entries.filter(source_type=source_type).first()


MOCK_CONSENT_TEMPLATE = "<div>Consent</div><div id='consent-form'>{{ consent_form }}</div>"
MOCK_STUDY_PAGE_HTML = "<h1>Test Study</h1>"


class StudyTestMixin:
    def setUp(self):
        self.user = User.objects.create_user(username='participant', password='testpass')
        self.profile = Profile.objects.create(user=self.user, user_type='participant')
        self.researcher_user = User.objects.create_user(username='researcher', password='testpass')
        self.researcher_profile = Profile.objects.create(user=self.researcher_user, user_type='researcher')
        self.study = Study.objects.create(
            title='Test Study',
            description='A test study',
            config_url='https://github.com/example/study-repo',
        )
        StudySourceConfiguration.objects.create(study=self.study, source_type='aware', status='required')
        StudySourceConfiguration.objects.create(study=self.study, source_type='json_url', status='optional')
        self.study.researchers.add(self.researcher_profile)
        self.study_participant = StudyParticipant.objects.create(
            participant=self.profile,
            study=self.study,
            researcher_approved=True,
        )
        self.client.login(username='participant', password='testpass')


# ---------------------------------------------------------------------------
# 1. StudyModelTest
# ---------------------------------------------------------------------------

class StudyModelTest(TestCase):

    def setUp(self):
        self.study = Study.objects.create(
            title='Model Study',
            description='desc',
            config_url='https://github.com/org/repo',
        )

    def test_str_returns_title(self):
        self.assertEqual(str(self.study), 'Model Study')

    def test_raw_content_base_url_github(self):
        self.study.config_url = 'https://github.com/org/repo'
        self.study.save()
        self.assertEqual(
            self.study.raw_content_base_url,
            'https://raw.githubusercontent.com/org/repo/main/'
        )

    def test_raw_content_base_url_plain(self):
        self.study.config_url = 'https://example.com/config'
        self.study.save()
        self.assertEqual(self.study.raw_content_base_url, 'https://example.com/config')

    def test_raw_content_base_url_gitlab(self):
        self.study.config_url = 'https://gitlab.com/org/repo'
        self.study.save()
        self.assertEqual(
            self.study.raw_content_base_url,
            'https://gitlab.com/org/repo/-/raw/main/'
        )

    def test_raw_content_base_url_custom_branch(self):
        self.study.config_url = 'https://gitlab.com/org/repo'
        self.study.repo_branch = 'develop'
        self.study.save()
        self.assertEqual(
            self.study.raw_content_base_url,
            'https://gitlab.com/org/repo/-/raw/develop/'
        )

    def test_raw_content_base_url_empty(self):
        self.study.config_url = ''
        self.study.save()
        self.assertIsNone(self.study.raw_content_base_url)




# ---------------------------------------------------------------------------
# 2. ConsentModelTest
# ---------------------------------------------------------------------------

class ConsentModelTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='participant2', password='testpass')
        self.profile = Profile.objects.create(user=self.user, user_type='participant')
        self.study = Study.objects.create(
            title='Consent Study',
            description='desc',
            config_url='https://github.com/org/repo',
        )
        self.source_config = StudySourceConfiguration.objects.create(
            study=self.study,
            source_type='aware',
            status='required',
        )
        self.study_participant = StudyParticipant.objects.create(
            participant=self.profile,
            study=self.study,
        )

    def test_str_format(self):
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=self.source_config,
            source_type='aware',
            study_participant=self.study_participant,
        )
        expected = f"Consent of participant {self.study_participant.pseudo_id} for {self.study.title}"
        self.assertEqual(str(consent), expected)
        self.assertNotIn(self.user.username, str(consent))

    def test_accept_configuration_snapshots_params(self):
        """accept_configuration copies all four fields and sets consent_date."""
        config_data = {'niimport_source_type': 'google_portability'}
        data_end = timezone.make_aware(datetime(2025, 12, 31))
        self.source_config.configuration = config_data
        self.source_config.requested_data_types = 'activity,posts'
        self.source_config.data_end = data_end
        self.source_config.data_start = timezone.make_aware(datetime(2024, 1, 1))
        self.source_config.save()

        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=self.source_config,
            source_type='aware',
            study_participant=self.study_participant,
        )
        consent.accept_configuration()
        consent.save()

        consent.refresh_from_db()
        self.assertEqual(consent.configuration, config_data)
        self.assertEqual(consent.requested_data_types, 'activity,posts')
        self.assertEqual(consent.data_end, data_end)
        self.assertEqual(consent.data_start.year, 2024)
        self.assertIsNotNone(consent.consent_date)
        self.assertTrue(consent.consent_text_accepted)

    def test_editing_config_after_consent_does_not_change_consent(self):
        """Mutating the StudySourceConfiguration after snapshotting must not affect the consent."""
        original_data_end = timezone.make_aware(datetime(2025, 6, 30))
        self.source_config.data_end = original_data_end
        self.source_config.configuration = {'key': 'original'}
        self.source_config.save()

        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=self.source_config,
            source_type='aware',
            study_participant=self.study_participant,
        )
        consent.accept_configuration()
        consent.save()

        # Mutate the config
        self.source_config.data_end = timezone.make_aware(datetime(2026, 1, 1))
        self.source_config.configuration = {'key': 'changed'}
        self.source_config.save()

        consent.refresh_from_db()
        self.assertEqual(consent.data_end, original_data_end)
        self.assertEqual(consent.configuration, {'key': 'original'})


# ---------------------------------------------------------------------------
# 2b. ConsentProfileDeletionTest
# ---------------------------------------------------------------------------

class ConsentProfileDeletionTest(TestCase):
    """Verify that deleting a participant profile does not destroy consent records."""

    def setUp(self):
        self.user = User.objects.create_user(username='deletion_test', password='testpass')
        self.profile = Profile.objects.create(user=self.user, user_type='participant')
        self.study = Study.objects.create(
            title='Deletion Study',
            description='desc',
            config_url='https://github.com/org/repo',
        )
        self.source_config = StudySourceConfiguration.objects.create(
            study=self.study,
            source_type='aware',
            status='required',
        )
        self.study_participant = StudyParticipant.objects.create(
            participant=self.profile,
            study=self.study,
        )
        self.consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=self.source_config,
            source_type='aware',
            study_participant=self.study_participant,
            is_complete=True,
            consent_text_accepted=True,
            consent_date=timezone.now(),
        )

    def test_consent_survives_profile_deletion(self):
        consent_pk = self.consent.pk
        self.profile.delete()
        self.assertTrue(Consent.objects.filter(pk=consent_pk).exists())

    def test_participant_field_set_to_null(self):
        self.profile.delete()
        self.consent.refresh_from_db()
        self.assertIsNone(self.consent.participant)

    def test_study_participant_link_preserved(self):
        pseudo_id = self.study_participant.pseudo_id
        self.profile.delete()
        self.consent.refresh_from_db()
        self.assertIsNotNone(self.consent.study_participant)
        self.assertEqual(self.consent.study_participant.pseudo_id, pseudo_id)

    def test_consent_fields_unchanged_after_deletion(self):
        consent_date = self.consent.consent_date
        self.profile.delete()
        self.consent.refresh_from_db()
        self.assertTrue(self.consent.is_complete)
        self.assertTrue(self.consent.consent_text_accepted)
        self.assertEqual(self.consent.source_type, 'aware')
        self.assertEqual(
            self.consent.consent_date.replace(microsecond=0),
            consent_date.replace(microsecond=0),
        )

    def test_str_after_profile_deletion(self):
        pseudo_id = self.study_participant.pseudo_id
        self.profile.delete()
        self.consent.refresh_from_db()
        result = str(self.consent)
        self.assertIn('deleted', result)
        self.assertIn(str(pseudo_id), result)

    def test_multiple_consents_across_studies_survive(self):
        study2 = Study.objects.create(
            title='Second Study', description='desc',
            config_url='https://github.com/org/repo2',
        )
        source_config2 = StudySourceConfiguration.objects.create(
            study=study2,
            source_type='json_url',
            status='optional',
        )
        sp2 = StudyParticipant.objects.create(
            participant=self.profile, study=study2,
        )
        consent2 = Consent.objects.create(
            participant=self.profile,
            study=study2,
            source_configuration=source_config2,
            source_type='json_url',
            study_participant=sp2,
        )
        self.profile.delete()
        self.consent.refresh_from_db()
        consent2.refresh_from_db()
        self.assertIsNone(self.consent.participant)
        self.assertIsNone(consent2.participant)
        self.assertIsNotNone(self.consent.study_participant)
        self.assertIsNotNone(consent2.study_participant)
        self.assertNotEqual(
            self.consent.study_participant.pseudo_id,
            consent2.study_participant.pseudo_id,
        )


# ---------------------------------------------------------------------------
# 3. StudyParticipantTest
# ---------------------------------------------------------------------------

class StudyParticipantTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='sp_participant', password='testpass')
        self.profile = Profile.objects.create(user=self.user, user_type='participant')
        self.study = Study.objects.create(
            title='SP Study',
            description='desc',
            config_url='https://github.com/org/repo',
        )

    def test_str_with_participant(self):
        sp = StudyParticipant.objects.create(participant=self.profile, study=self.study)
        self.assertIn(str(sp.pseudo_id), str(sp))
        self.assertNotIn(self.user.username, str(sp))

    def test_str_after_profile_deletion(self):
        sp = StudyParticipant.objects.create(participant=self.profile, study=self.study)
        pseudo_id = sp.pseudo_id
        self.profile.delete()
        sp.refresh_from_db()
        self.assertIsNone(sp.participant)
        self.assertEqual(sp.pseudo_id, pseudo_id)
        self.assertIn(str(pseudo_id), str(sp))
        self.assertNotIn(self.user.username, str(sp))

    def test_unique_per_study(self):
        StudyParticipant.objects.create(participant=self.profile, study=self.study)
        with self.assertRaises(Exception):
            StudyParticipant.objects.create(participant=self.profile, study=self.study)

    def test_different_pseudo_id_per_study(self):
        study2 = Study.objects.create(
            title='SP Study 2',
            description='desc',
            config_url='https://github.com/org/repo2',
        )
        sp1 = StudyParticipant.objects.create(participant=self.profile, study=self.study)
        sp2 = StudyParticipant.objects.create(participant=self.profile, study=study2)
        self.assertNotEqual(sp1.pseudo_id, sp2.pseudo_id)

    def test_survives_profile_deletion(self):
        sp = StudyParticipant.objects.create(participant=self.profile, study=self.study)
        pseudo_id = sp.pseudo_id
        self.profile.delete()
        sp.refresh_from_db()
        self.assertIsNone(sp.participant)
        self.assertEqual(sp.pseudo_id, pseudo_id)


# ---------------------------------------------------------------------------
# 4. JoinStudyViewTest
# ---------------------------------------------------------------------------

class JoinStudyViewTest(StudyTestMixin, TestCase):

    def test_get_shows_confirmation_without_enrolling(self):
        # Clicking Join (GET) must not create any consent records.
        url = reverse('join_study')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Consent.objects.filter(participant=self.profile, study=self.study).exists()
        )

    def test_post_without_confirmation_does_not_enroll(self):
        url = reverse('join_study')
        self.client.post(url, {})
        self.assertFalse(
            Consent.objects.filter(participant=self.profile, study=self.study).exists()
        )

    def _unapprove(self):
        self.study_participant.researcher_approved = False
        self.study_participant.save()

    def test_creates_required_consents(self):
        url = reverse('join_study')
        self.client.post(url, {'accept_terms': 'on'})
        consent = Consent.objects.filter(
            participant=self.profile,
            study=self.study,
            source_type='aware',
            is_optional=False,
        )
        self.assertTrue(consent.exists())

    def test_creates_optional_consents(self):
        url = reverse('join_study')
        self.client.post(url, {'accept_terms': 'on'})
        consent = Consent.objects.filter(
            participant=self.profile,
            study=self.study,
            source_type='json_url',
            is_optional=True,
        )
        self.assertTrue(consent.exists())

    def test_redirects_to_consent_workflow(self):
        url = reverse('join_study')
        response = self.client.post(url, {'accept_terms': 'on'})
        expected_url = reverse('consent_workflow')
        self.assertRedirects(response, expected_url, fetch_redirect_response=False)

    def test_unapproved_join_shows_pending_without_consents(self):
        # Participant is not yet approved: registration is noted but no consents
        # are created and the pending page is shown.
        self._unapprove()
        url = reverse('join_study')
        response = self.client.post(url, {'accept_terms': 'on'})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studies/registration_pending.html')
        self.assertFalse(
            Consent.objects.filter(participant=self.profile, study=self.study).exists()
        )

    def test_consent_workflow_blocked_until_approved(self):
        # Directly hitting the consent workflow while unapproved shows the pending
        # page and creates no consents.
        self._unapprove()
        response = self.client.get(reverse('consent_workflow'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'studies/registration_pending.html')
        self.assertFalse(
            Consent.objects.filter(participant=self.profile, study=self.study).exists()
        )

    def test_consent_workflow_creates_consents_after_approval(self):
        # An approved participant reaching the workflow directly (without re-join)
        # gets their consents created.
        self.client.get(reverse('consent_workflow'))
        self.assertTrue(
            Consent.objects.filter(participant=self.profile, study=self.study).exists()
        )

    def test_requires_login(self):
        self.client.logout()
        url = reverse('join_study')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])

    def test_no_study_configured_404(self):
        # Delete all studies to trigger Http404 from get_study()
        Study.objects.all().delete()
        url = reverse('join_study')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_creates_study_participant(self):
        url = reverse('join_study')
        self.client.post(url, {'accept_terms': 'on'})
        sp = StudyParticipant.objects.filter(
            participant=self.profile,
            study=self.study,
        )
        self.assertTrue(sp.exists())
        self.assertIsNotNone(sp.first().pseudo_id)

    def test_consent_has_study_participant(self):
        url = reverse('join_study')
        self.client.post(url, {'accept_terms': 'on'})
        consent = Consent.objects.filter(
            participant=self.profile,
            study=self.study,
        ).first()
        self.assertIsNotNone(consent.study_participant)
        self.assertIsNotNone(consent.study_participant.pseudo_id)

    def test_joining_twice_does_not_duplicate_consents(self):
        url = reverse('join_study')
        self.client.post(url, {'accept_terms': 'on'})
        self.client.post(url, {'accept_terms': 'on'})
        # Exactly one active consent per source configuration, not two.
        self.assertEqual(
            Consent.objects.filter(
                participant=self.profile, study=self.study,
                source_type='aware', revocation_date__isnull=True,
            ).count(),
            1,
        )
        self.assertEqual(
            Consent.objects.filter(
                participant=self.profile, study=self.study,
                source_type='json_url', revocation_date__isnull=True,
            ).count(),
            1,
        )


# ---------------------------------------------------------------------------
# 5. ConsentCheckboxViewTest
# ---------------------------------------------------------------------------

class ConsentCheckboxViewTest(StudyTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.source_config = get_source_config(self.study, 'aware')
        self.consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=self.source_config,
            source_type='aware',
            consent_text_accepted=False,
            is_complete=False,
            study_participant=self.study_participant,
        )

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_get_renders_consent_form(self, mock_template):
        url = reverse('consent_workflow')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'consent-form')

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_saves_consent_text_accepted_to_db(self, mock_template):
        """REGRESSION TEST: POST with accept_consent must persist consent_text_accepted=True to DB."""
        url = reverse('consent_workflow')
        self.client.post(url, {'accept_consent': True})
        refreshed = Consent.objects.get(pk=self.consent.pk)
        self.assertTrue(refreshed.consent_text_accepted)
        self.assertIsNotNone(refreshed.consent_date)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_missing_checkbox_does_not_save(self, mock_template):
        url = reverse('consent_workflow')
        response = self.client.post(url, {})
        self.assertEqual(response.status_code, 200)
        refreshed = Consent.objects.get(pk=self.consent.pk)
        self.assertFalse(refreshed.consent_text_accepted)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_redirects_to_workflow_with_consent_id(self, mock_template):
        url = reverse('consent_workflow')
        response = self.client.post(url, {'accept_consent': True})
        self.assertEqual(response.status_code, 302)
        self.assertIn(str(self.consent.id), response['Location'])

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_with_data_source_marks_complete(self, mock_template):
        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Test Aware Source',
            status='active',
        )
        self.consent.data_source = source
        self.consent.save()

        url = reverse('consent_workflow')
        self.client.post(url, {'accept_consent': True})

        refreshed = Consent.objects.get(pk=self.consent.pk)
        self.assertTrue(refreshed.is_complete)
        self.assertIsNotNone(refreshed.consent_date)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_data_start_from_config(self, mock_template):
        StudySourceConfiguration.objects.update_or_create(
            study=self.study,
            source_type='aware',
            defaults={'status': 'required', 'data_start': timezone.make_aware(datetime(2024, 1, 1))},
        )
        # refresh consent so source_configuration FK sees updated config
        self.consent.refresh_from_db()

        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Test Aware Config Source',
            status='active',
        )
        self.consent.data_source = source
        self.consent.save()

        url = reverse('consent_workflow')
        self.client.post(url, {'accept_consent': True})

        refreshed = Consent.objects.get(pk=self.consent.pk)
        self.assertIsNotNone(refreshed.data_start)
        self.assertEqual(refreshed.data_start.year, 2024)
        self.assertEqual(refreshed.data_start.month, 1)
        self.assertIsNotNone(refreshed.consent_date)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_data_start_fallback_to_consent_date(self, mock_template):

        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Test Aware Fallback Source',
            status='active',
        )
        self.consent.data_source = source
        self.consent.save()

        url = reverse('consent_workflow')
        self.client.post(url, {'accept_consent': True})

        refreshed = Consent.objects.get(pk=self.consent.pk)
        self.assertIsNotNone(refreshed.data_start)
        self.assertIsNotNone(refreshed.consent_date)
        self.assertEqual(
            refreshed.data_start.replace(microsecond=0),
            refreshed.consent_date.replace(microsecond=0),
        )
        # consent_date is set by accept_configuration
        self.assertIsNotNone(refreshed.consent_date)


# ---------------------------------------------------------------------------
# 6. SelectDataSourceViewTest
# ---------------------------------------------------------------------------

class SelectDataSourceViewTest(StudyTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.source_config = get_source_config(self.study, 'aware')
        self.consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=self.source_config,
            source_type='aware',
            consent_text_accepted=True,
            is_complete=False,
            study_participant=self.study_participant,
        )
        self.source = AwareDataSource.objects.create(
            profile=self.profile,
            name='My Aware Source',
            status='active',
        )

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_get_renders_selection_form(self, mock_template):
        url = reverse('consent_workflow')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_select_links_data_source(self, mock_template):
        url = reverse('consent_workflow')
        self.client.post(url, {'action': 'select', 'source_id': self.source.id})
        refreshed = Consent.objects.get(pk=self.consent.pk)
        self.assertEqual(refreshed.data_source, self.source)
        self.assertTrue(refreshed.is_complete)
        self.assertIsNotNone(refreshed.consent_date)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_select_redirects_to_workflow(self, mock_template):
        url = reverse('consent_workflow')
        response = self.client.post(url, {'action': 'select', 'source_id': self.source.id})
        self.assertEqual(response.status_code, 302)
        expected_url = reverse('consent_workflow')
        self.assertIn(expected_url, response['Location'])

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_create_redirects_to_add_data_source(self, mock_template):
        url = reverse('consent_workflow')
        response = self.client.post(url, {'action': 'create'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/data-sources/add/', response['Location'])
        self.assertIn('consent_id', response['Location'])

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_empty_source_id_does_not_complete(self, mock_template):
        url = reverse('consent_workflow')
        self.client.post(url, {'action': 'select', 'source_id': ''})
        refreshed = Consent.objects.get(pk=self.consent.pk)
        self.assertFalse(refreshed.is_complete)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_source_not_matching_consent_is_rejected(self, mock_template):
        # A niimport consent for the tiktok variant must not be satisfied by a
        # google source, even if the id is POSTed directly.
        niimport_config = StudySourceConfiguration.objects.create(
            study=self.study, source_type='niimport', status='optional',
            configuration={'niimport_source_type': 'tiktok_portability'},
        )
        niimport_consent = Consent.objects.create(
            participant=self.profile, study=self.study,
            source_configuration=niimport_config, source_type='niimport',
            consent_text_accepted=True, is_complete=False,
            configuration={'niimport_source_type': 'tiktok_portability'},
            study_participant=self.study_participant,
        )
        google_source = NiimportDataSource.objects.create(
            profile=self.profile, name='Google Source', status='active',
            niimport_source_type='google_portability',
            configuration={'niimport_source_type': 'google_portability'},
        )
        url = f"{reverse('consent_workflow')}?consent_id={niimport_consent.id}"
        self.client.post(url, {'action': 'select', 'source_id': google_source.id})
        refreshed = Consent.objects.get(pk=niimport_consent.pk)
        self.assertFalse(refreshed.is_complete)
        self.assertIsNone(refreshed.data_source)


# ---------------------------------------------------------------------------
# 7. ConsentWorkflowOrchestratorTest
# ---------------------------------------------------------------------------

class ConsentWorkflowOrchestratorTest(StudyTestMixin, TestCase):

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_redirects_to_dashboard_when_no_incomplete_consents(self, mock_template):
        url = reverse('consent_workflow')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('dashboard', response['Location'])

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_routes_to_checkbox_when_text_not_accepted(self, mock_template):
        source_config = get_source_config(self.study, 'aware')
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='aware',
            consent_text_accepted=False,
            is_complete=False,
            study_participant=self.study_participant,
        )
        url = reverse('consent_workflow')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'consent-form')

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_routes_to_create_flow_when_no_sources_exist(self, mock_template):
        source_config = get_source_config(self.study, 'aware')
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='aware',
            consent_text_accepted=True,
            is_complete=False,
            study_participant=self.study_participant,
        )
        url = reverse('consent_workflow')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/data-sources/add/', response['Location'])

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_routes_to_select_view_when_sources_exist(self, mock_template):
        source_config = get_source_config(self.study, 'aware')
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='aware',
            consent_text_accepted=True,
            is_complete=False,
            study_participant=self.study_participant,
        )
        AwareDataSource.objects.create(
            profile=self.profile,
            name='Existing Source',
            status='active',
        )
        url = reverse('consent_workflow')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_routes_to_select_view_filters_niimport_sources_by_configuration(self, mock_template):
        # Create niimport config with specific source type filter
        source_config = StudySourceConfiguration.objects.create(
            study=self.study,
            source_type='niimport',
            status='optional',
            configuration={'niimport_source_type': 'tiktok_portability'},
        )
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='niimport',
            consent_text_accepted=True,
            # Snapshot copied at acceptance (accept_configuration); matching uses this.
            configuration={'niimport_source_type': 'tiktok_portability'},
            is_complete=False,
            study_participant=self.study_participant,
        )
        NiimportDataSource.objects.create(
            profile=self.profile,
            name='Matching TikTok Source',
            status='active',
            niimport_source_type='tiktok_portability',
            configuration={'niimport_source_type': 'tiktok_portability'},
        )
        NiimportDataSource.objects.create(
            profile=self.profile,
            name='Non-matching Google Source',
            status='active',
            niimport_source_type='google_portability',
            configuration={'niimport_source_type': 'google_portability'},
        )
        url = reverse('consent_workflow')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Matching TikTok Source')
        self.assertNotContains(response, 'Non-matching Google Source')

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_consent_id_param_targets_specific_consent(self, mock_template):
        source_config = get_source_config(self.study, 'aware')
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='aware',
            consent_text_accepted=False,
            is_complete=False,
            study_participant=self.study_participant,
        )
        second_consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='aware',
            consent_text_accepted=False,
            is_complete=False,
            study_participant=self.study_participant,
        )
        url = reverse('consent_workflow')
        response = self.client.get(url, {'consent_id': second_consent.id})
        self.assertEqual(response.status_code, 200)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_optional_only_consents_redirect_to_dashboard(self, mock_template):
        source_config = get_source_config(self.study, 'json_url')
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='json_url',
            consent_text_accepted=False,
            is_complete=False,
            is_optional=True,
            study_participant=self.study_participant,
        )
        url = reverse('consent_workflow')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('dashboard', response['Location'])


# ---------------------------------------------------------------------------
# 8. WithdrawFromStudyViewTest
# ---------------------------------------------------------------------------

class WithdrawFromStudyViewTest(StudyTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.source_config = get_source_config(self.study, 'aware')
        self.source1 = AwareDataSource.objects.create(
            profile=self.profile,
            name='Withdraw Source 1',
            status='active',
        )
        self.source2 = AwareDataSource.objects.create(
            profile=self.profile,
            name='Withdraw Source 2',
            status='active',
        )
        self.consent1 = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=self.source_config,
            source_type='aware',
            data_source=self.source1,
            is_complete=True,
            consent_date=timezone.now(),
            study_participant=self.study_participant,
        )
        self.consent2 = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=self.source_config,
            source_type='aware',
            data_source=self.source2,
            is_complete=True,
            consent_date=timezone.now(),
            study_participant=self.study_participant,
        )

    def test_get_renders_confirmation(self):
        url = reverse('withdraw_from_study')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_post_revokes_all_active_consents(self):
        url = reverse('withdraw_from_study')
        self.client.post(url)
        self.consent1.refresh_from_db()
        self.consent2.refresh_from_db()
        self.assertIsNotNone(self.consent1.revocation_date)
        self.assertIsNone(self.consent1.data_source)
        self.assertFalse(self.consent1.is_complete)
        self.assertIsNotNone(self.consent2.revocation_date)
        self.assertIsNone(self.consent2.data_source)
        self.assertFalse(self.consent2.is_complete)

    def test_post_skips_already_revoked(self):
        revoked_time = timezone.now() - timezone.timedelta(days=1)
        source_config = get_source_config(self.study, 'aware')
        revoked_consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='aware',
            is_complete=False,
            revocation_date=revoked_time,
            study_participant=self.study_participant,
        )
        url = reverse('withdraw_from_study')
        self.client.post(url)
        revoked_consent.refresh_from_db()
        # The revocation_date should still be the original, not updated
        self.assertEqual(
            revoked_consent.revocation_date.replace(microsecond=0),
            revoked_time.replace(microsecond=0),
        )

    def test_post_redirects_to_dashboard(self):
        url = reverse('withdraw_from_study')
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('dashboard', response['Location'])

    def test_requires_login(self):
        self.client.logout()
        url = reverse('withdraw_from_study')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])


# ---------------------------------------------------------------------------
# 9. RevokeConsentViewTest
# ---------------------------------------------------------------------------

class RevokeConsentViewTest(StudyTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.source_config = get_source_config(self.study, 'aware')
        self.source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Revoke Source',
            status='active',
        )
        self.consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=self.source_config,
            source_type='aware',
            data_source=self.source,
            is_complete=True,
            is_optional=True,
            consent_date=timezone.now(),
            study_participant=self.study_participant,
        )

    def test_get_optional_renders_confirmation(self):
        url = reverse('revoke_consent', args=[self.consent.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_post_optional_revokes_and_creates_new(self):
        url = reverse('revoke_consent', args=[self.consent.id])
        self.client.post(url)
        self.consent.refresh_from_db()
        self.assertIsNotNone(self.consent.revocation_date)
        self.assertIsNone(self.consent.data_source)
        self.assertFalse(self.consent.is_complete)
        new_consent = Consent.objects.filter(
            participant=self.profile,
            study=self.study,
            source_type='aware',
            is_optional=True,
            revocation_date__isnull=True,
        ).first()
        self.assertIsNotNone(new_consent)
        self.assertNotEqual(new_consent.pk, self.consent.pk)

    def test_post_redirects_to_dashboard(self):
        url = reverse('revoke_consent', args=[self.consent.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('dashboard', response['Location'])

    def test_cannot_revoke_other_users_consent(self):
        other_user = User.objects.create_user(username='other_participant', password='testpass')
        Profile.objects.create(user=other_user, user_type='participant')
        self.client.login(username='other_participant', password='testpass')
        url = reverse('revoke_consent', args=[self.consent.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# 10. StudyDetailViewTest
# ---------------------------------------------------------------------------

class StudyDetailViewTest(StudyTestMixin, TestCase):

    @patch('studies.services.get_study_page_html', return_value=MOCK_STUDY_PAGE_HTML)
    def test_renders_for_anonymous_user(self, mock_page):
        self.client.logout()
        url = reverse('study_detail')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    @patch('studies.services.get_study_page_html', return_value=MOCK_STUDY_PAGE_HTML)
    def test_user_in_study_true_with_active_consent(self, mock_page):
        source_config = get_source_config(self.study, 'aware')
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='aware',
            is_complete=True,
            consent_date=timezone.now(),
            study_participant=self.study_participant,
        )
        url = reverse('study_detail')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.study.title)

    @patch('studies.services.get_study_page_html', return_value=MOCK_STUDY_PAGE_HTML)
    def test_user_in_study_false_with_revoked_consent(self, mock_page):
        source_config = get_source_config(self.study, 'aware')
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='aware',
            is_complete=False,
            revocation_date=timezone.now(),
            study_participant=self.study_participant,
        )
        url = reverse('study_detail')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    @patch('studies.services.get_study_page_html', return_value=MOCK_STUDY_PAGE_HTML)
    def test_study_detail_loads(self, mock_page):
        url = reverse('study_detail')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# 11. GetNextConsentHelperTest
# ---------------------------------------------------------------------------

class GetNextConsentHelperTest(StudyTestMixin, TestCase):

    def test_returns_specific_consent_by_id(self):
        source_config = get_source_config(self.study, 'aware')
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='aware',
            is_complete=False,
            is_optional=False,
            study_participant=self.study_participant,
        )
        result = get_next_consent(self.profile, self.study, consent_id=consent.id)
        self.assertEqual(result, consent)

    def test_404_for_nonexistent_id(self):
        self.assertRaises(Http404, get_next_consent, self.profile, self.study, consent_id=99999)

    def test_returns_first_incomplete_required(self):
        source_config = get_source_config(self.study, 'aware')
        consent1 = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='aware',
            is_complete=False,
            is_optional=False,
            study_participant=self.study_participant,
        )
        consent2 = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='aware',
            is_complete=False,
            is_optional=False,
            study_participant=self.study_participant,
        )
        result = get_next_consent(self.profile, self.study)
        self.assertIsNotNone(result)
        self.assertIn(result, [consent1, consent2])

    def test_returns_none_when_all_complete(self):
        source_config = get_source_config(self.study, 'aware')
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='aware',
            is_complete=True,
            is_optional=False,
            consent_date=timezone.now(),
            study_participant=self.study_participant,
        )
        result = get_next_consent(self.profile, self.study)
        self.assertIsNone(result)

    def test_ignores_optional_consents(self):
        source_config = get_source_config(self.study, 'json_url')
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=source_config,
            source_type='json_url',
            is_complete=False,
            is_optional=True,
            study_participant=self.study_participant,
        )
        result = get_next_consent(self.profile, self.study)
        self.assertIsNone(result)

    def test_ignores_revoked_consents(self):
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='aware',
            is_complete=False,
            is_optional=False,
            revocation_date=timezone.now(),
            study_participant=self.study_participant,
        )
        result = get_next_consent(self.profile, self.study)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 12. StudyDataApiTest
# ---------------------------------------------------------------------------

class StudyDataApiTest(StudyTestMixin, TestCase):

    def test_unauthorized_user_403(self):
        # participant (not researcher) should get 403
        url = reverse('study_data_api')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_researcher_can_access(self):
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_superuser_can_access(self):
        superuser = User.objects.create_superuser(
            username='superadmin', password='testpass', email='admin@example.com'
        )
        self.client.login(username='superadmin', password='testpass')
        url = reverse('study_data_api')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_returns_data_types_when_no_type_specified(self):
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('study', data)
        self.assertIn('data_types', data)
        self.assertNotIn('data', data)
        self.assertNotIn('data_count', data)

    def test_no_study_returns_404(self):
        Study.objects.all().delete()
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    @patch.object(AwareDataSource, 'count_rows', return_value=1)
    @patch.object(AwareDataSource, 'fetch_data', return_value=[{'timestamp': 123, 'value': 42}])
    @patch.object(AwareDataSource, 'get_data_types', return_value=['battery'])
    def test_data_rows_include_participant_id(self, mock_types, mock_fetch, mock_count):
        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='API Test Source',
            status='active',
        )
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='aware',
            data_source=source,
            source_configuration=get_source_config(self.study, 'aware'),
            is_complete=True,
            consent_date=timezone.now(),
            data_start=timezone.now(),
            study_participant=self.study_participant,
        )
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        pid = str(self.study_participant.pseudo_id)
        response = self.client.get(url, {'data_type': 'aware:battery', 'participant_id': pid})
        data = response.json()
        self.assertGreater(data['count'], 0)
        for row in data['data']:
            self.assertEqual(row['participant_id'], pid)
            self.assertEqual(row['source_type'], 'aware')

    @patch.object(AwareDataSource, 'count_rows', return_value=1)
    @patch.object(AwareDataSource, 'fetch_data', return_value=[{'timestamp': 123, 'value': 42}])
    @patch.object(AwareDataSource, 'get_data_types', return_value=['battery', 'location'])
    def test_unrequested_data_type_is_not_returned(self, mock_types, mock_fetch, mock_count):
        # Participant consented only to 'battery'; 'location' must not be downloadable.
        source = AwareDataSource.objects.create(
            profile=self.profile, name='Restricted Source', status='active',
        )
        Consent.objects.create(
            participant=self.profile, study=self.study, source_type='aware',
            data_source=source, source_configuration=get_source_config(self.study, 'aware'),
            is_complete=True, consent_date=timezone.now(), data_start=timezone.now(),
            requested_data_types='battery',
            study_participant=self.study_participant,
        )
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        pid = str(self.study_participant.pseudo_id)

        # 'location' is not in the allowed types — no matching consent/source → 404
        response = self.client.get(url, {'data_type': 'aware:location', 'participant_id': pid})
        self.assertEqual(response.status_code, 404)
        mock_fetch.assert_not_called()

        # 'battery' is allowed → 200 with data
        response = self.client.get(url, {'data_type': 'aware:battery', 'participant_id': pid})
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json()['count'], 0)

    @patch.object(AwareDataSource, 'get_data_types', return_value=['battery', 'location'])
    def test_discovery_hides_unrequested_data_types(self, mock_types):
        source = AwareDataSource.objects.create(
            profile=self.profile, name='Discovery Source', status='active',
        )
        Consent.objects.create(
            participant=self.profile, study=self.study, source_type='aware',
            data_source=source, source_configuration=get_source_config(self.study, 'aware'),
            is_complete=True, consent_date=timezone.now(), data_start=timezone.now(),
            requested_data_types='battery',
            study_participant=self.study_participant,
        )
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        response = self.client.get(url)
        self.assertEqual(response.json()['data_types'], ['aware:battery'])

    @patch.object(AwareDataSource, 'count_rows', return_value=1)
    @patch.object(AwareDataSource, 'fetch_data', return_value=[{'timestamp': 123, 'value': 42}])
    @patch.object(AwareDataSource, 'get_data_types', return_value=['battery'])
    def test_config_data_start_limits_fetched_data(self, mock_types, mock_fetch, mock_count):
        # Consent snapshot data_start (June 1) is later than consent_date (Jan 1), so fetch should use June 1
        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Config Start Test Source',
            status='active',
        )
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='aware',
            data_source=source,
            source_configuration=get_source_config(self.study, 'aware'),
            is_complete=True,
            consent_date=timezone.make_aware(datetime(2024, 1, 1)),
            data_start=timezone.make_aware(datetime(2024, 6, 1)),
            study_participant=self.study_participant,
        )
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        pid = str(self.study_participant.pseudo_id)
        # No timestamp query param → effective_cursor == data_start_ms (June 1 clamps it up)
        self.client.get(url, {'data_type': 'aware:battery', 'participant_id': pid})

        self.assertEqual(
            mock_fetch.call_args.kwargs['timestamp'],
            expected_ms(date(2024, 6, 1)),
        )

    @patch.object(AwareDataSource, 'get_data_types', return_value=['battery'])
    def test_config_data_end_limits_fetched_data(self, mock_types):
        # Consent snapshot data_end (Sep 1) post-caps rows: October row is dropped.
        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Config End Test Source',
            status='active',
        )
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='aware',
            data_source=source,
            source_configuration=get_source_config(self.study, 'aware'),
            is_complete=True,
            consent_date=timezone.make_aware(datetime(2024, 1, 1)),
            data_start=timezone.make_aware(datetime(2024, 1, 1)),
            data_end=timezone.make_aware(datetime(2024, 9, 1)),
            study_participant=self.study_participant,
        )
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        pid = str(self.study_participant.pseudo_id)

        aug_ms = expected_ms(date(2024, 8, 1))
        oct_ms = expected_ms(date(2024, 10, 1))
        two_rows = [{'timestamp': aug_ms, 'value': 1}, {'timestamp': oct_ms, 'value': 2}]

        with patch.object(AwareDataSource, 'fetch_data', return_value=two_rows):
            response = self.client.get(url, {'data_type': 'aware:battery', 'participant_id': pid})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        # Only the August row survives the post-cap; the October row is beyond data_end
        self.assertEqual(data['count'], 1)
        surviving_ts = data['data'][0]['timestamp']
        self.assertLessEqual(surviving_ts, expected_ms(date(2024, 9, 2)))

    @patch.object(AwareDataSource, 'count_rows', return_value=1)
    @patch.object(AwareDataSource, 'fetch_data', return_value=[{'timestamp': 123, 'value': 42}])
    @patch.object(AwareDataSource, 'get_data_types', return_value=['battery'])
    def test_editing_config_does_not_change_consent_window(self, mock_types, mock_fetch, mock_count):
        # Changing the config's data_start AFTER consent must NOT affect subsequent API calls:
        # the consent snapshot is the authoritative record.
        source_config = StudySourceConfiguration.objects.update_or_create(
            study=self.study,
            source_type='aware',
            defaults={'status': 'required', 'data_start': timezone.make_aware(datetime(2024, 6, 1))},
        )[0]

        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Config Change Test Source',
            status='active',
        )
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='aware',
            data_source=source,
            source_configuration=source_config,
            is_complete=True,
            consent_date=timezone.make_aware(datetime(2024, 1, 1)),
            data_start=timezone.make_aware(datetime(2024, 6, 1)),
            study_participant=self.study_participant,
        )
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        pid = str(self.study_participant.pseudo_id)

        self.client.get(url, {'data_type': 'aware:battery', 'participant_id': pid})
        self.assertEqual(
            mock_fetch.call_args.kwargs['timestamp'],
            expected_ms(date(2024, 6, 1)),
        )

        # Now edit the config to a different date — the consent snapshot must stay unchanged
        source_config.data_start = timezone.make_aware(datetime(2024, 8, 1))
        source_config.save()

        self.client.get(url, {'data_type': 'aware:battery', 'participant_id': pid})
        # Still uses the original snapshotted June 1, not the edited August 1
        self.assertEqual(
            mock_fetch.call_args.kwargs['timestamp'],
            expected_ms(date(2024, 6, 1)),
        )

    @patch.object(AwareDataSource, 'count_rows', return_value=1)
    @patch.object(AwareDataSource, 'fetch_data', return_value=[{'timestamp': 123, 'value': 42}])
    @patch.object(AwareDataSource, 'get_data_types', return_value=['battery'])
    def test_query_timestamp_overrides_when_after_data_start(self, mock_types, mock_fetch, mock_count):
        # timestamp query param (March 1) is after consent data_start (Jan 1),
        # so effective_cursor == the query timestamp, not data_start_ms
        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Query Timestamp Test Source',
            status='active',
        )
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='aware',
            data_source=source,
            source_configuration=get_source_config(self.study, 'aware'),
            is_complete=True,
            consent_date=timezone.make_aware(datetime(2024, 1, 1)),
            data_start=timezone.make_aware(datetime(2024, 1, 1)),
            study_participant=self.study_participant,
        )
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        pid = str(self.study_participant.pseudo_id)
        march_ms = expected_ms(date(2024, 3, 1))
        self.client.get(url, {'data_type': 'aware:battery', 'participant_id': pid, 'timestamp': march_ms})

        # effective_cursor == march_ms (> data_start_ms for Jan 1)
        self.assertEqual(mock_fetch.call_args.kwargs['timestamp'], march_ms)

    @patch.object(AwareDataSource, 'count_rows', return_value=1)
    @patch.object(AwareDataSource, 'fetch_data', return_value=[{'timestamp': 123, 'value': 42}])
    @patch.object(AwareDataSource, 'get_data_types', return_value=['battery'])
    def test_no_config_dates_falls_back_to_consent(self, mock_types, mock_fetch, mock_count):
        # Without source_configurations, fetch_data should use the consent's data_start as cursor
        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='No Config Fallback Test Source',
            status='active',
        )
        consent_start = timezone.make_aware(datetime(2024, 1, 1))
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='aware',
            data_source=source,
            source_configuration=get_source_config(self.study, 'aware'),
            is_complete=True,
            consent_date=consent_start,
            data_start=consent_start,
            study_participant=self.study_participant,
        )
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        pid = str(self.study_participant.pseudo_id)
        self.client.get(url, {'data_type': 'aware:battery', 'participant_id': pid})

        self.assertEqual(
            mock_fetch.call_args.kwargs['timestamp'],
            expected_ms(date(2024, 1, 1)),
        )


# ---------------------------------------------------------------------------
# 12b. ConsentTemplateServiceTest
# ---------------------------------------------------------------------------

class ConsentTemplateServiceTest(StudyTestMixin, TestCase):

    def test_config_consent_template_html_takes_precedence(self):
        from . import services
        config = get_source_config(self.study, 'aware')
        config.consent_template_html = '<p>Custom consent for aware</p>'
        config.save()
        result = services.get_consent_template(self.study, 'aware', config)
        self.assertEqual(result, '<p>Custom consent for aware</p>')

    @patch('studies.services.requests.get')
    def test_falls_back_to_repo_when_html_empty(self, mock_get):
        from . import services
        mock_get.return_value.text = '<p>From repo</p>'
        mock_get.return_value.raise_for_status = lambda: None
        config = get_source_config(self.study, 'aware')  # consent_template_html is ''
        result = services.get_consent_template(self.study, 'aware', config)
        self.assertEqual(result, '<p>From repo</p>')


# ---------------------------------------------------------------------------
# 13. StudyAdminTest
# ---------------------------------------------------------------------------

class StudyAdminTest(TestCase):

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username='admin', password='testpass', email='admin@example.com',
        )
        Profile.objects.create(user=self.superuser, user_type='researcher')
        self.researcher_user = User.objects.create_user(username='researcher', password='testpass')
        self.researcher_profile = Profile.objects.create(
            user=self.researcher_user, user_type='researcher',
        )
        self.study = Study.objects.create(
            title='Admin Study',
            description='desc',
            config_url='https://github.com/org/repo',
        )
        self.client.login(username='admin', password='testpass')

    def _post_study_change(self, study, researchers):
        url = reverse('admin:studies_study_change', args=[study.id])
        return self.client.post(url, {
            'title': study.title,
            'description': study.description,
            'config_url': study.config_url,
            'researchers': [r.id for r in researchers],
            'assets-TOTAL_FORMS': '0',
            'assets-INITIAL_FORMS': '0',
            'assets-MIN_NUM_FORMS': '0',
            'assets-MAX_NUM_FORMS': '1000',
            'source_config_entries-TOTAL_FORMS': '0',
            'source_config_entries-INITIAL_FORMS': '0',
            'source_config_entries-MIN_NUM_FORMS': '0',
            'source_config_entries-MAX_NUM_FORMS': '1000',
            'participations-TOTAL_FORMS': '0',
            'participations-INITIAL_FORMS': '0',
            'participations-MIN_NUM_FORMS': '0',
            'participations-MAX_NUM_FORMS': '1000',
        })

    def test_superuser_can_add_researcher_to_study(self):
        response = self._post_study_change(self.study, [self.researcher_profile])
        self.assertEqual(response.status_code, 302)
        self.study.refresh_from_db()
        self.assertIn(self.researcher_profile, self.study.researchers.all())

    def _make_staff_researcher(self, user):
        """Add user to the Researchers group and mark as staff."""
        from django.contrib.auth.models import Group
        user.is_staff = True
        user.save()
        group, _ = Group.objects.get_or_create(name='Researchers')
        user.groups.add(group)

    def test_researcher_in_study_can_add_another_researcher(self):
        self._make_staff_researcher(self.researcher_user)
        self.study.researchers.add(self.researcher_profile)
        new_researcher_user = User.objects.create_user(
            username='new_researcher', password='testpass',
        )
        new_researcher_profile = Profile.objects.create(
            user=new_researcher_user, user_type='researcher',
        )
        self.client.login(username='researcher', password='testpass')
        response = self._post_study_change(
            self.study, [self.researcher_profile, new_researcher_profile],
        )
        self.assertEqual(response.status_code, 302)
        self.study.refresh_from_db()
        self.assertIn(new_researcher_profile, self.study.researchers.all())

    def test_researcher_not_in_study_cannot_access_change_form(self):
        self._make_staff_researcher(self.researcher_user)
        # researcher_profile is NOT added to study.researchers
        self.client.login(username='researcher', password='testpass')
        url = reverse('admin:studies_study_change', args=[self.study.id])
        response = self.client.get(url)
        # Django admin returns 302 (redirect to admin index) for objects not in queryset
        self.assertNotEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# 14. StudyDataApiWatermarkTest
# ---------------------------------------------------------------------------

class StudyDataApiWatermarkTest(StudyTestMixin, TestCase):
    """Tests for the per-source slug-namespaced download endpoint with cursor/advance logic."""

    def setUp(self):
        super().setUp()
        import uuid
        from rest_framework.test import APIClient
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.researcher_user)

        self.source = AwareDataSource.objects.create(
            profile=self.profile,
            name='watermark-aware-src',
            status='active',
            device_id=uuid.uuid4(),
        )
        self.consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=get_source_config(self.study, 'aware'),
            data_source=self.source,
            source_type='aware',
            is_complete=True,
            consent_date=timezone.now(),
            # Null data_start/end keeps data_start_ms=0 so cursor math is clean.
            data_start=None,
            data_end=None,
            study_participant=self.study_participant,
        )
        self.pid = str(self.study_participant.pseudo_id)

    # ------------------------------------------------------------------
    # 1. List branch returns namespaced types.
    # ------------------------------------------------------------------
    def test_list_branch_returns_namespaced_data_types(self):
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']):
            resp = self.api_client.get(reverse('study_data_api'))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('data_types', data)
        self.assertIn('aware:battery', data['data_types'])

    # ------------------------------------------------------------------
    # 2. Fetch without participant_id → 400.
    # ------------------------------------------------------------------
    def test_fetch_without_participant_id_returns_400(self):
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']):
            resp = self.api_client.get(
                reverse('study_data_api'),
                {'data_type': 'aware:battery'},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('participant_id', resp.json().get('error', ''))

    # ------------------------------------------------------------------
    # 3. Fetch with un-namespaced data_type → 400.
    # ------------------------------------------------------------------
    def test_fetch_with_un_namespaced_data_type_returns_400(self):
        resp = self.api_client.get(
            reverse('study_data_api'),
            {'data_type': 'battery', 'participant_id': self.pid},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('namespaced', resp.json().get('error', ''))

    # ------------------------------------------------------------------
    # 4. Fetch with unknown participant_id → 404.
    # ------------------------------------------------------------------
    def test_fetch_with_unknown_participant_id_returns_404(self):
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']):
            resp = self.api_client.get(
                reverse('study_data_api'),
                {'data_type': 'aware:battery', 'participant_id': 'does-not-exist'},
            )
        self.assertEqual(resp.status_code, 404)

    # ------------------------------------------------------------------
    # 5. Happy fetch (no advance): enriched rows, no watermark created.
    # ------------------------------------------------------------------
    def test_happy_fetch_no_advance_returns_enriched_rows_and_no_watermark(self):
        from data_sources.models import DeletableWatermark
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'fetch_data', return_value=[
                 {'timestamp': 3000}, {'timestamp': 5000}
             ]):
            resp = self.api_client.get(
                reverse('study_data_api'),
                {'data_type': 'aware:battery', 'participant_id': self.pid, 'timestamp': 0},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['count'], 2)
        self.assertEqual(len(data['data']), 2)
        for row in data['data']:
            self.assertEqual(row['data_type'], 'aware:battery')
            self.assertEqual(row['participant_id'], self.pid)
        self.assertFalse(
            DeletableWatermark.objects.filter(data_source=self.source, data_type='battery').exists()
        )

    # ------------------------------------------------------------------
    # 6. advance=true, timestamp=0: creates watermark with max timestamp.
    # ------------------------------------------------------------------
    def test_advance_from_zero_creates_watermark_with_max_timestamp(self):
        from data_sources.models import DeletableWatermark
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'fetch_data', return_value=[
                 {'timestamp': 3000}, {'timestamp': 5000}
             ]):
            resp = self.api_client.get(
                reverse('study_data_api'),
                {'data_type': 'aware:battery', 'participant_id': self.pid,
                 'timestamp': 0, 'advance': 'true'},
            )
        self.assertEqual(resp.status_code, 200)
        wm = DeletableWatermark.objects.get(data_source=self.source, data_type='battery')
        self.assertEqual(wm.downloaded_through, 5000)

    # ------------------------------------------------------------------
    # 7. Monotonic re-download: second advance=true with timestamp=0 (≤ floor+1)
    #    keeps downloaded_through at 5000, response is 200.
    # ------------------------------------------------------------------
    def test_advance_monotonic_redownload_stays_at_max(self):
        from data_sources.models import DeletableWatermark
        # First call establishes D=5000.
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'fetch_data', return_value=[
                 {'timestamp': 3000}, {'timestamp': 5000}
             ]):
            self.api_client.get(
                reverse('study_data_api'),
                {'data_type': 'aware:battery', 'participant_id': self.pid,
                 'timestamp': 0, 'advance': 'true'},
            )
        # Second call with timestamp=0 again (0 <= 5000+1): not a gap, max stays 5000.
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'fetch_data', return_value=[
                 {'timestamp': 3000}, {'timestamp': 5000}
             ]):
            resp = self.api_client.get(
                reverse('study_data_api'),
                {'data_type': 'aware:battery', 'participant_id': self.pid,
                 'timestamp': 0, 'advance': 'true'},
            )
        self.assertEqual(resp.status_code, 200)
        wm = DeletableWatermark.objects.get(data_source=self.source, data_type='battery')
        self.assertEqual(wm.downloaded_through, 5000)

    # ------------------------------------------------------------------
    # 8. Contiguous continuation: D=5000, timestamp=5001 (== floor+1).
    #    New rows go up to 8000 → downloaded_through advances to 8000.
    # ------------------------------------------------------------------
    def test_advance_contiguous_continuation_advances_watermark(self):
        from data_sources.models import DeletableWatermark
        # Establish D=5000.
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'fetch_data', return_value=[
                 {'timestamp': 3000}, {'timestamp': 5000}
             ]):
            self.api_client.get(
                reverse('study_data_api'),
                {'data_type': 'aware:battery', 'participant_id': self.pid,
                 'timestamp': 0, 'advance': 'true'},
            )
        # Contiguous next page: timestamp=5001 = floor+1.
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'fetch_data', return_value=[{'timestamp': 8000}]):
            resp = self.api_client.get(
                reverse('study_data_api'),
                {'data_type': 'aware:battery', 'participant_id': self.pid,
                 'timestamp': 5001, 'advance': 'true'},
            )
        self.assertEqual(resp.status_code, 200)
        wm = DeletableWatermark.objects.get(data_source=self.source, data_type='battery')
        self.assertEqual(wm.downloaded_through, 8000)

    # ------------------------------------------------------------------
    # 9. Gap: D=5000, timestamp=10000 (> floor+1=5001) → 400, watermark unchanged.
    # ------------------------------------------------------------------
    def test_advance_gap_returns_400_and_watermark_unchanged(self):
        from data_sources.models import DeletableWatermark
        # Establish D=5000.
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'fetch_data', return_value=[
                 {'timestamp': 3000}, {'timestamp': 5000}
             ]):
            self.api_client.get(
                reverse('study_data_api'),
                {'data_type': 'aware:battery', 'participant_id': self.pid,
                 'timestamp': 0, 'advance': 'true'},
            )
        # Gap request: timestamp=10000 > 5001.
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'fetch_data', return_value=[{'timestamp': 12000}]):
            resp = self.api_client.get(
                reverse('study_data_api'),
                {'data_type': 'aware:battery', 'participant_id': self.pid,
                 'timestamp': 10000, 'advance': 'true'},
            )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn('gap', body.get('error', ''))
        self.assertEqual(body.get('downloaded_through'), 5000)
        # Watermark must be unchanged.
        wm = DeletableWatermark.objects.get(data_source=self.source, data_type='battery')
        self.assertEqual(wm.downloaded_through, 5000)

    # ------------------------------------------------------------------
    # 10. data_end cap: rows after data_end_ms are dropped.
    #     data_end = 2026-01-01 → data_end_ms ≈ end-of-day 2026-01-01 in local tz.
    #     Row at 1_700_000_000_000 (Nov 2023) is before → kept.
    #     Row at 1_800_000_000_000 (Jan 2027) is after → dropped.
    # ------------------------------------------------------------------
    def test_data_end_cap_drops_rows_after_cutoff(self):
        self.consent.data_end = timezone.make_aware(datetime(2026, 1, 1))
        self.consent.save()
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'fetch_data', return_value=[
                 {'timestamp': 1_700_000_000_000},
                 {'timestamp': 1_800_000_000_000},
             ]):
            resp = self.api_client.get(
                reverse('study_data_api'),
                {'data_type': 'aware:battery', 'participant_id': self.pid, 'timestamp': 0},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['data'][0]['timestamp'], 1_700_000_000_000)


# ---------------------------------------------------------------------------
# 15. MarkDataDeletableTest
# ---------------------------------------------------------------------------

class MarkDataDeletableTest(StudyTestMixin, TestCase):
    """Tests for the mark_data_deletable POST endpoint with slug-namespaced data_type."""

    def setUp(self):
        super().setUp()
        import uuid
        from rest_framework.test import APIClient
        from data_sources.models import DeletableWatermark

        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.researcher_user)

        self.source = AwareDataSource.objects.create(
            profile=self.profile,
            name='deletable-aware-src',
            status='active',
            device_id=uuid.uuid4(),
        )
        self.consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=get_source_config(self.study, 'aware'),
            data_source=self.source,
            source_type='aware',
            is_complete=True,
            consent_date=timezone.now(),
            data_start=None,
            data_end=None,
            study_participant=self.study_participant,
        )
        # Pre-create a watermark keyed on the BARE data_type ('battery').
        self.watermark = DeletableWatermark.objects.create(
            data_source=self.source,
            data_type='battery',
            downloaded_through=5000,
        )

    # setUp creates: self.watermark with downloaded_through=5000, deletable_through=None.
    # Happy-path values: D=5000, through=3000, M=5000 → D>=through, M>through → success.

    def _post(self, data_type='aware:battery', through=3000, client=None):
        c = client or self.api_client
        return c.post(
            reverse('mark_data_deletable'),
            {'data_type': data_type, 'through': through},
            format='json',
        )

    # ------------------------------------------------------------------
    # 0. Un-namespaced data_type → 400 with namespacing error.
    # ------------------------------------------------------------------
    def test_un_namespaced_data_type_returns_400(self):
        resp = self.api_client.post(
            reverse('mark_data_deletable'),
            {'data_type': 'battery', 'through': 3000},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('namespaced', resp.json().get('error', ''))

    # ------------------------------------------------------------------
    # 1. Happy path: marked True, DB row updated to supplied through.
    # ------------------------------------------------------------------
    def test_marks_deletable_through_and_returns_marked_true(self):
        from data_sources.models import DeletableWatermark
        # D=5000, through=3000, M=5000 → D>=through, M>through → success
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'latest_timestamp', return_value=5000), \
             patch.object(AwareDataSource, 'mark_deletable'):
            resp = self._post(through=3000)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['marked_count'], 1)
        result = data['results'][0]
        self.assertTrue(result['marked'])
        self.assertEqual(result['deletable_through'], 3000)

        self.watermark.refresh_from_db()
        self.assertEqual(self.watermark.deletable_through, 3000)

    # ------------------------------------------------------------------
    # 2. through == existing deletable_through is allowed.
    # ------------------------------------------------------------------
    def test_through_equal_to_existing_deletable_through_still_marks_true(self):
        self.watermark.deletable_through = 3000
        self.watermark.save()
        # D=5000, through=3000, M=5000 → succeeds even when deletable_through already set.
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'latest_timestamp', return_value=5000), \
             patch.object(AwareDataSource, 'mark_deletable'):
            resp = self._post(through=3000)
        self.assertEqual(resp.status_code, 200)
        result = resp.json()['results'][0]
        self.assertTrue(result['marked'])
        self.assertEqual(result['deletable_through'], 3000)

    # ------------------------------------------------------------------
    # 3. Missing through → 400.
    # ------------------------------------------------------------------
    def test_missing_through_returns_400(self):
        resp = self.api_client.post(
            reverse('mark_data_deletable'),
            {'data_type': 'aware:battery'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get('error'), 'through is required')

    # ------------------------------------------------------------------
    # 4. Non-integer through → 400.
    # ------------------------------------------------------------------
    def test_non_integer_through_returns_400(self):
        resp = self.api_client.post(
            reverse('mark_data_deletable'),
            {'data_type': 'aware:battery', 'through': 'abc'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get('error'), 'through must be an integer (Unix ms)')

    # ------------------------------------------------------------------
    # 5. Missing data_type → 400.
    # ------------------------------------------------------------------
    def test_missing_data_type_returns_400(self):
        resp = self.api_client.post(
            reverse('mark_data_deletable'),
            {'through': 3000},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get('error'), 'data_type is required')

    # ------------------------------------------------------------------
    # 6. D < through → 'data not downloaded through cutoff'.
    # ------------------------------------------------------------------
    def test_downloaded_through_less_than_through_returns_not_downloaded_reason(self):
        # D=2000 < through=3000 → not downloaded through cutoff
        self.watermark.downloaded_through = 2000
        self.watermark.save()
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']):
            resp = self._post(through=3000)
        self.assertEqual(resp.status_code, 200)
        result = resp.json()['results'][0]
        self.assertFalse(result['marked'])
        self.assertEqual(result['reason'], 'data not downloaded through cutoff')

    # ------------------------------------------------------------------
    # 7. M <= through (M == through, closed boundary) → 'no data newer than cutoff'.
    # ------------------------------------------------------------------
    def test_latest_timestamp_equal_to_through_returns_no_newer_data_reason(self):
        # D=5000, through=5000, M=5000 → M <= through → no data newer than cutoff
        self.watermark.downloaded_through = 5000
        self.watermark.save()
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'latest_timestamp', return_value=5000):
            resp = self._post(through=5000)
        self.assertEqual(resp.status_code, 200)
        result = resp.json()['results'][0]
        self.assertFalse(result['marked'])
        self.assertEqual(result['reason'], 'no data newer than cutoff')

    # ------------------------------------------------------------------
    # 8. M is None → 'no data newer than cutoff'.
    # ------------------------------------------------------------------
    def test_latest_timestamp_none_returns_no_newer_data_reason(self):
        # D=5000, through=3000, M=None → no data newer than cutoff
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'latest_timestamp', return_value=None):
            resp = self._post(through=3000)
        self.assertEqual(resp.status_code, 200)
        result = resp.json()['results'][0]
        self.assertFalse(result['marked'])
        self.assertEqual(result['reason'], 'no data newer than cutoff')

    # ------------------------------------------------------------------
    # 9a. No watermark row at all → 'no downloaded data'.
    # ------------------------------------------------------------------
    def test_no_watermark_at_all_returns_no_downloaded_data_reason(self):
        self.watermark.delete()
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']):
            resp = self._post(through=3000)
        self.assertEqual(resp.status_code, 200)
        result = resp.json()['results'][0]
        self.assertFalse(result['marked'])
        self.assertEqual(result['reason'], 'no downloaded data')

    # ------------------------------------------------------------------
    # 9b. Watermark exists but downloaded_through is None → 'no downloaded data'.
    # ------------------------------------------------------------------
    def test_downloaded_through_none_returns_no_downloaded_data_reason(self):
        self.watermark.downloaded_through = None
        self.watermark.save()
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']):
            resp = self._post(through=3000)
        self.assertEqual(resp.status_code, 200)
        result = resp.json()['results'][0]
        self.assertFalse(result['marked'])
        self.assertEqual(result['reason'], 'no downloaded data')

    # ------------------------------------------------------------------
    # 10. supports_deletion() returns False → 'source does not support deletion'.
    # ------------------------------------------------------------------
    def test_source_not_supporting_deletion_returns_appropriate_reason(self):
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'supports_deletion', return_value=False), \
             patch.object(AwareDataSource, 'latest_timestamp') as mock_latest, \
             patch.object(AwareDataSource, 'mark_deletable') as mock_mark:
            resp = self._post(through=3000)
        self.assertEqual(resp.status_code, 200)
        result = resp.json()['results'][0]
        self.assertFalse(result['marked'])
        self.assertEqual(result['reason'], 'source does not support deletion')
        # latest_timestamp and mark_deletable must not be consulted.
        mock_latest.assert_not_called()
        mock_mark.assert_not_called()

    # ------------------------------------------------------------------
    # 11. Hook called with BARE name and correct through on success.
    # ------------------------------------------------------------------
    def test_mark_deletable_hook_called_with_bare_name_and_correct_args(self):
        # D=5000, through=3000, M=5000; hook must receive bare 'battery', not 'aware:battery'.
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'latest_timestamp', return_value=5000), \
             patch.object(AwareDataSource, 'mark_deletable') as mock_mark:
            resp = self._post(through=3000)
        self.assertEqual(resp.status_code, 200)
        mock_mark.assert_called_once_with('battery', through=3000)

    # ------------------------------------------------------------------
    # 12. Hook raises → 'source error'; watermark deletable_through unchanged.
    # ------------------------------------------------------------------
    def test_hook_raises_returns_source_error_and_watermark_unchanged(self):
        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery']), \
             patch.object(AwareDataSource, 'latest_timestamp', return_value=5000), \
             patch.object(AwareDataSource, 'mark_deletable', side_effect=Exception('boom')):
            resp = self._post(through=3000)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        result = data['results'][0]
        self.assertFalse(result['marked'])
        self.assertEqual(result['reason'], 'source error')
        self.assertIn('boom', result.get('detail', ''))

        # Watermark deletable_through must NOT have been set.
        self.watermark.refresh_from_db()
        self.assertIsNone(self.watermark.deletable_through)

    # ------------------------------------------------------------------
    # 13. Non-researcher → 403.
    # ------------------------------------------------------------------
    def test_non_researcher_gets_403(self):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.user)  # participant, not researcher
        resp = self._post(client=client)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get('error'), 'Unauthorized')

    # ------------------------------------------------------------------
    # 14. requested_data_types restriction skips a disallowed consent.
    # ------------------------------------------------------------------
    def test_allowed_data_types_restriction_skips_disallowed_consent(self):
        """A consent with requested_data_types set must not expose a non-consented type."""
        # Restrict this consent to 'location' only; a 'battery' request must skip it.
        self.consent.requested_data_types = 'location'
        self.consent.save()

        with patch.object(AwareDataSource, 'get_data_types', return_value=['battery', 'location']), \
             patch.object(AwareDataSource, 'latest_timestamp', return_value=5000), \
             patch.object(AwareDataSource, 'mark_deletable') as mock_mark:
            resp = self._post(data_type='aware:battery', through=3000)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # The consent is skipped, so results should be empty and mark_deletable not called.
        self.assertEqual(data['results'], [])
        mock_mark.assert_not_called()


# ----------------------------------------------------------------------------
# UnmarkDataDeletableTest
# ----------------------------------------------------------------------------
class UnmarkDataDeletableTest(StudyTestMixin, TestCase):
    """Tests for the unmark_data_deletable POST endpoint."""

    def setUp(self):
        super().setUp()
        import uuid
        from rest_framework.test import APIClient
        from data_sources.models import DeletableWatermark

        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.researcher_user)

        self.source = AwareDataSource.objects.create(
            profile=self.profile,
            name='deletable-aware-src',
            status='active',
            device_id=uuid.uuid4(),
        )
        self.consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_configuration=get_source_config(self.study, 'aware'),
            data_source=self.source,
            source_type='aware',
            is_complete=True,
            consent_date=timezone.now(),
            data_start=None,
            data_end=None,
            study_participant=self.study_participant,
        )
        # Pre-create a watermark keyed on the BARE data_type ('battery').
        self.watermark = DeletableWatermark.objects.create(
            data_source=self.source,
            data_type='battery',
            downloaded_through=5000,
            deletable_through=3000,
        )

    def _post(self, client=None):
        c = client or self.api_client
        return c.post(reverse('unmark_data_deletable'), {}, format='json')

    # ------------------------------------------------------------------
    # 1. Happy path: unmarked True, watermark cleared.
    # ------------------------------------------------------------------
    def test_unmarks_and_clears_watermark(self):
        with patch.object(AwareDataSource, 'unmark_deletable') as mock_unmark:
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['unmarked_count'], 1)
        result = data['results'][0]
        self.assertTrue(result['unmarked'])
        self.assertEqual(result['watermarks_cleared'], 1)
        mock_unmark.assert_called_once_with()

        self.watermark.refresh_from_db()
        self.assertIsNone(self.watermark.deletable_through)
        self.assertEqual(self.watermark.downloaded_through, 5000)

    # ------------------------------------------------------------------
    # 2. Hook raises → 'source error'; watermark deletable_through unchanged.
    # ------------------------------------------------------------------
    def test_source_error_keeps_watermark(self):
        with patch.object(AwareDataSource, 'unmark_deletable', side_effect=Exception('boom')):
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['unmarked_count'], 0)
        result = data['results'][0]
        self.assertFalse(result['unmarked'])
        self.assertEqual(result['reason'], 'source error')
        self.assertIn('boom', result.get('detail', ''))

        self.watermark.refresh_from_db()
        self.assertEqual(self.watermark.deletable_through, 3000)

    # ------------------------------------------------------------------
    # 3. Non-researcher → 403.
    # ------------------------------------------------------------------
    def test_non_researcher_forbidden(self):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.user)  # participant, not researcher
        resp = self._post(client=client)
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # 4. Watermark already null → unmarked True, watermarks_cleared 0.
    # ------------------------------------------------------------------
    def test_watermark_already_null_reports_zero_cleared(self):
        self.watermark.deletable_through = None
        self.watermark.save()
        with patch.object(AwareDataSource, 'unmark_deletable'):
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        result = resp.json()['results'][0]
        self.assertTrue(result['unmarked'])
        self.assertEqual(result['watermarks_cleared'], 0)

    # ------------------------------------------------------------------
    # 5. Source without deletion support → skipped entirely.
    # ------------------------------------------------------------------
    def test_source_without_deletion_support_skipped(self):
        with patch.object(AwareDataSource, 'supports_deletion', return_value=False), \
             patch.object(AwareDataSource, 'unmark_deletable') as mock_unmark:
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['results'], [])
        self.assertEqual(data['unmarked_count'], 0)
        mock_unmark.assert_not_called()


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SendParticipantEmailAdminTest(StudyTestMixin, TestCase):
    """The participant admin 'Send email' view sends synchronously via the
    in-memory backend; the study contact email is used as Reply-To."""

    def setUp(self):
        super().setUp()
        self.user.email = 'participant@example.com'
        self.user.save()
        self.study.contact_email = 'contact@example.com'
        self.study.save()
        self.admin_user = User.objects.create_superuser(
            'admin', 'admin@example.com', 'adminpass'
        )
        self.client.force_login(self.admin_user)
        self.url = reverse(
            'admin:studies_studyparticipant_send_email',
            args=[self.study_participant.pk],
        )

    def test_get_renders_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_post_sends_email_with_reply_to(self):
        response = self.client.post(
            self.url,
            {'subject': 'Hello', 'message': 'Body text',
             'reply_to': 'contact@example.com'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.subject, 'Hello')
        self.assertEqual(msg.body, 'Body text')
        self.assertEqual(msg.to, ['participant@example.com'])
        self.assertEqual(msg.reply_to, ['contact@example.com'])
        # From stays on the authenticated domain, never the participant address.
        self.assertEqual(msg.from_email, settings.DEFAULT_FROM_EMAIL)

    def test_post_can_reply_to_own_email(self):
        # The sender may choose their own address instead of the study contact.
        self.client.post(
            self.url,
            {'subject': 'Hi', 'message': 'Body',
             'reply_to': 'admin@example.com'},
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].reply_to, ['admin@example.com'])

    def test_reply_to_must_be_an_offered_address(self):
        # An address that is neither the study contact nor the sender's own is
        # rejected by the choice field; nothing is sent.
        response = self.client.post(
            self.url,
            {'subject': 'Hi', 'message': 'Body',
             'reply_to': 'attacker@example.com'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_post_without_any_reply_address_has_no_reply_to(self):
        # With no study contact and no sender email, the field is dropped and
        # the message goes out with no Reply-To.
        self.study.contact_email = ''
        self.study.save()
        self.admin_user.email = ''
        self.admin_user.save()
        self.client.post(self.url, {'subject': 'Hi', 'message': 'Body'})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].reply_to, [])

    def test_post_participant_without_email_sends_nothing(self):
        self.user.email = ''
        self.user.save()
        self.client.post(
            self.url,
            {'subject': 'Hi', 'message': 'Body',
             'reply_to': 'contact@example.com'},
        )
        self.assertEqual(len(mail.outbox), 0)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BulkSendParticipantEmailAdminTest(StudyTestMixin, TestCase):
    """The changelist 'Send an email to selected participants' action writes one
    email and sends a separate copy to each selected participant."""

    def setUp(self):
        super().setUp()
        self.user.email = 'p1@example.com'
        self.user.save()
        self.study.contact_email = 'contact@example.com'
        self.study.save()
        # A second participant in the same study.
        self.user2 = User.objects.create_user(
            username='participant2', password='testpass', email='p2@example.com'
        )
        self.profile2 = Profile.objects.create(
            user=self.user2, user_type='participant'
        )
        self.participant2 = StudyParticipant.objects.create(
            participant=self.profile2, study=self.study
        )
        self.admin_user = User.objects.create_superuser(
            'admin', 'admin@example.com', 'adminpass'
        )
        self.client.force_login(self.admin_user)
        self.changelist_url = reverse(
            'admin:studies_studyparticipant_changelist'
        )
        self.selected = [self.study_participant.pk, self.participant2.pk]

    def _post(self, extra):
        data = {
            'action': 'send_bulk_email',
            ACTION_CHECKBOX_NAME: self.selected,
            **extra,
        }
        return self.client.post(self.changelist_url, data)

    def test_confirmation_page_renders_without_sending(self):
        # Selecting the action (no 'send' marker) shows the form, sends nothing.
        response = self._post({})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_bulk_sends_to_all_selected(self):
        response = self._post({
            'send': '1',
            'subject': 'Hello all',
            'message': 'Body',
            'reply_to': 'contact@example.com',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)
        recipients = {msg.to[0] for msg in mail.outbox}
        self.assertEqual(recipients, {'p1@example.com', 'p2@example.com'})
        for msg in mail.outbox:
            self.assertEqual(msg.subject, 'Hello all')
            self.assertEqual(msg.reply_to, ['contact@example.com'])
            # Recipients are never disclosed to each other.
            self.assertEqual(len(msg.to), 1)

    def test_bulk_can_reply_to_own_email(self):
        self._post({
            'send': '1',
            'subject': 'Hi',
            'message': 'Body',
            'reply_to': 'admin@example.com',
        })
        self.assertEqual(len(mail.outbox), 2)
        for msg in mail.outbox:
            self.assertEqual(msg.reply_to, ['admin@example.com'])

    def test_bulk_skips_participants_without_email(self):
        self.user2.email = ''
        self.user2.save()
        self._post({
            'send': '1',
            'subject': 'Hi',
            'message': 'Body',
            'reply_to': 'contact@example.com',
        })
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['p1@example.com'])

    def test_action_queryset_is_scoped_to_researchers_studies(self):
        # The bulk action relies on get_queryset to keep researchers from
        # emailing participants of studies they don't run.
        from django.contrib.admin.sites import site
        from django.test import RequestFactory
        from .admin import StudyParticipantAdmin

        other_study = Study.objects.create(
            title='Other', description='x',
            config_url='https://github.com/example/other',
        )
        other_user = User.objects.create_user(
            username='outsider', password='testpass', email='outsider@example.com'
        )
        other_profile = Profile.objects.create(
            user=other_user, user_type='participant'
        )
        outsider = StudyParticipant.objects.create(
            participant=other_profile, study=other_study
        )
        request = RequestFactory().get('/')
        request.user = self.researcher_user
        model_admin = StudyParticipantAdmin(StudyParticipant, site)
        qs = model_admin.get_queryset(request)
        self.assertIn(self.study_participant, qs)
        self.assertNotIn(outsider, qs)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SendParticipantEmailByIdsAdminTest(StudyTestMixin, TestCase):
    """The 'Email participants by pseudo-ID' view emails a cohort identified by
    pasted pseudo-IDs, matched against the researcher-scoped queryset."""

    def setUp(self):
        super().setUp()
        self.user.email = 'p1@example.com'
        self.user.save()
        self.study.contact_email = 'contact@example.com'
        self.study.save()
        self.user2 = User.objects.create_user(
            username='participant2', password='testpass', email='p2@example.com'
        )
        self.profile2 = Profile.objects.create(
            user=self.user2, user_type='participant'
        )
        self.participant2 = StudyParticipant.objects.create(
            participant=self.profile2, study=self.study
        )
        self.admin_user = User.objects.create_superuser(
            'admin', 'admin@example.com', 'adminpass'
        )
        self.client.force_login(self.admin_user)
        self.url = reverse(
            'admin:studies_studyparticipant_send_email_by_ids'
        )

    def test_get_renders_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_sends_to_pasted_ids(self):
        pasted = f'{self.study_participant.pseudo_id}\n{self.participant2.pseudo_id}'
        response = self.client.post(self.url, {
            'pseudo_ids': pasted,
            'subject': 'Cohort',
            'message': 'Body',
            'reply_to': 'contact@example.com',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)
        recipients = {msg.to[0] for msg in mail.outbox}
        self.assertEqual(recipients, {'p1@example.com', 'p2@example.com'})

    def test_unknown_and_invalid_ids_are_skipped(self):
        # One real ID, one well-formed but unknown UUID, one garbage token.
        pasted = (
            f'{self.study_participant.pseudo_id}, '
            f'{uuid.uuid4()}, not-a-uuid'
        )
        response = self.client.post(self.url, {
            'pseudo_ids': pasted,
            'subject': 'Hi',
            'message': 'Body',
            'reply_to': 'contact@example.com',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['p1@example.com'])

    def test_no_valid_ids_is_a_form_error(self):
        response = self.client.post(self.url, {
            'pseudo_ids': 'not-a-uuid, also-bad',
            'subject': 'Hi',
            'message': 'Body',
            'reply_to': 'contact@example.com',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

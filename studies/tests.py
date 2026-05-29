from datetime import datetime, timedelta
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone
from django.http import Http404

from users.models import Profile
from data_sources.models.aware import AwareDataSource
from data_sources.models.jsonurl import JsonUrlDataSource
from .models import Study, Consent, StudyParticipant, StudySourceConfiguration
from .views import get_next_consent


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
        StudySourceConfiguration.objects.create(study=self.study, source_type='AwareDataSource', status='required')
        StudySourceConfiguration.objects.create(study=self.study, source_type='JsonUrlDataSource', status='optional')
        self.study.researchers.add(self.researcher_profile)
        self.study_participant = StudyParticipant.objects.create(
            participant=self.profile,
            study=self.study,
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

    def test_get_source_dates(self):
        StudySourceConfiguration.objects.create(
            study=self.study,
            source_type='AwareDataSource',
            status='required',
            data_start=timezone.make_aware(datetime(2024, 1, 1)),
            data_end=timezone.make_aware(datetime(2024, 12, 31)),
        )
        start, end = self.study.get_source_dates('AwareDataSource')
        self.assertEqual(start.year, 2024)
        self.assertEqual(start.month, 1)
        self.assertEqual(end.year, 2024)
        self.assertEqual(end.month, 12)

    def test_get_source_dates_missing_source_type(self):
        start, end = self.study.get_source_dates('AwareDataSource')
        self.assertIsNone(start)
        self.assertIsNone(end)


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
        self.study_participant = StudyParticipant.objects.create(
            participant=self.profile,
            study=self.study,
        )

    def test_str_format(self):
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            study_participant=self.study_participant,
        )
        expected = f"Consent of {self.user.username} for {self.study.title}"
        self.assertEqual(str(consent), expected)


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
        self.study_participant = StudyParticipant.objects.create(
            participant=self.profile,
            study=self.study,
        )
        self.consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
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
        self.assertEqual(self.consent.source_type, 'AwareDataSource')
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
        sp2 = StudyParticipant.objects.create(
            participant=self.profile, study=study2,
        )
        consent2 = Consent.objects.create(
            participant=self.profile,
            study=study2,
            source_type='JsonUrlDataSource',
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
        self.assertIn(self.user.username, str(sp))

    def test_str_after_profile_deletion(self):
        sp = StudyParticipant.objects.create(participant=self.profile, study=self.study)
        pseudo_id = sp.pseudo_id
        self.profile.delete()
        sp.refresh_from_db()
        self.assertIsNone(sp.participant)
        self.assertEqual(sp.pseudo_id, pseudo_id)
        self.assertIn('deleted', str(sp))

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

    def test_creates_required_consents(self):
        url = reverse('join_study', args=[self.study.id])
        self.client.get(url)
        consent = Consent.objects.filter(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            is_optional=False,
        )
        self.assertTrue(consent.exists())

    def test_creates_optional_consents(self):
        url = reverse('join_study', args=[self.study.id])
        self.client.get(url)
        consent = Consent.objects.filter(
            participant=self.profile,
            study=self.study,
            source_type='JsonUrlDataSource',
            is_optional=True,
        )
        self.assertTrue(consent.exists())

    def test_redirects_to_consent_workflow(self):
        url = reverse('join_study', args=[self.study.id])
        response = self.client.get(url)
        expected_url = reverse('consent_workflow', args=[self.study.id])
        self.assertRedirects(response, expected_url, fetch_redirect_response=False)

    def test_requires_login(self):
        self.client.logout()
        url = reverse('join_study', args=[self.study.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])

    def test_nonexistent_study_404(self):
        url = reverse('join_study', args=[99999])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_creates_study_participant(self):
        url = reverse('join_study', args=[self.study.id])
        self.client.get(url)
        sp = StudyParticipant.objects.filter(
            participant=self.profile,
            study=self.study,
        )
        self.assertTrue(sp.exists())
        self.assertIsNotNone(sp.first().pseudo_id)

    def test_consent_has_study_participant(self):
        url = reverse('join_study', args=[self.study.id])
        self.client.get(url)
        consent = Consent.objects.filter(
            participant=self.profile,
            study=self.study,
        ).first()
        self.assertIsNotNone(consent.study_participant)
        self.assertIsNotNone(consent.study_participant.pseudo_id)


# ---------------------------------------------------------------------------
# 5. ConsentCheckboxViewTest
# ---------------------------------------------------------------------------

class ConsentCheckboxViewTest(StudyTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            consent_text_accepted=False,
            is_complete=False,
            study_participant=self.study_participant,
        )

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_get_renders_consent_form(self, mock_template):
        url = reverse('consent_workflow', args=[self.study.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'consent-form')

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_saves_consent_text_accepted_to_db(self, mock_template):
        """REGRESSION TEST: POST with accept_consent must persist consent_text_accepted=True to DB."""
        url = reverse('consent_workflow', args=[self.study.id])
        self.client.post(url, {'accept_consent': True})
        refreshed = Consent.objects.get(pk=self.consent.pk)
        self.assertTrue(refreshed.consent_text_accepted)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_missing_checkbox_does_not_save(self, mock_template):
        url = reverse('consent_workflow', args=[self.study.id])
        response = self.client.post(url, {})
        self.assertEqual(response.status_code, 200)
        refreshed = Consent.objects.get(pk=self.consent.pk)
        self.assertFalse(refreshed.consent_text_accepted)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_redirects_to_workflow_with_consent_id(self, mock_template):
        url = reverse('consent_workflow', args=[self.study.id])
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

        url = reverse('consent_workflow', args=[self.study.id])
        self.client.post(url, {'accept_consent': True})

        refreshed = Consent.objects.get(pk=self.consent.pk)
        self.assertTrue(refreshed.is_complete)
        self.assertIsNotNone(refreshed.consent_date)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_data_start_from_config(self, mock_template):
        StudySourceConfiguration.objects.update_or_create(
            study=self.study,
            source_type='AwareDataSource',
            defaults={'status': 'required', 'data_start': timezone.make_aware(datetime(2024, 1, 1))},
        )

        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Test Aware Config Source',
            status='active',
        )
        self.consent.data_source = source
        self.consent.save()

        url = reverse('consent_workflow', args=[self.study.id])
        self.client.post(url, {'accept_consent': True})

        refreshed = Consent.objects.get(pk=self.consent.pk)
        self.assertIsNotNone(refreshed.data_start)
        self.assertEqual(refreshed.data_start.year, 2024)
        self.assertEqual(refreshed.data_start.month, 1)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_data_start_fallback_to_consent_date(self, mock_template):
        self.study.source_config_entries.filter(source_type='AwareDataSource').delete()

        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Test Aware Fallback Source',
            status='active',
        )
        self.consent.data_source = source
        self.consent.save()

        url = reverse('consent_workflow', args=[self.study.id])
        self.client.post(url, {'accept_consent': True})

        refreshed = Consent.objects.get(pk=self.consent.pk)
        self.assertIsNotNone(refreshed.data_start)
        self.assertIsNotNone(refreshed.consent_date)
        self.assertEqual(
            refreshed.data_start.replace(microsecond=0),
            refreshed.consent_date.replace(microsecond=0),
        )


# ---------------------------------------------------------------------------
# 6. SelectDataSourceViewTest
# ---------------------------------------------------------------------------

class SelectDataSourceViewTest(StudyTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
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
        url = reverse('consent_workflow', args=[self.study.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_select_links_data_source(self, mock_template):
        url = reverse('consent_workflow', args=[self.study.id])
        self.client.post(url, {'action': 'select', 'source_id': self.source.id})
        refreshed = Consent.objects.get(pk=self.consent.pk)
        self.assertEqual(refreshed.data_source, self.source)
        self.assertTrue(refreshed.is_complete)
        self.assertIsNotNone(refreshed.consent_date)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_select_redirects_to_workflow(self, mock_template):
        url = reverse('consent_workflow', args=[self.study.id])
        response = self.client.post(url, {'action': 'select', 'source_id': self.source.id})
        self.assertEqual(response.status_code, 302)
        expected_url = reverse('consent_workflow', args=[self.study.id])
        self.assertIn(expected_url, response['Location'])

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_create_redirects_to_add_data_source(self, mock_template):
        url = reverse('consent_workflow', args=[self.study.id])
        response = self.client.post(url, {'action': 'create'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/data-sources/add/', response['Location'])
        self.assertIn('consent_id', response['Location'])

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_post_empty_source_id_does_not_complete(self, mock_template):
        url = reverse('consent_workflow', args=[self.study.id])
        self.client.post(url, {'action': 'select', 'source_id': ''})
        refreshed = Consent.objects.get(pk=self.consent.pk)
        self.assertFalse(refreshed.is_complete)


# ---------------------------------------------------------------------------
# 7. ConsentWorkflowOrchestratorTest
# ---------------------------------------------------------------------------

class ConsentWorkflowOrchestratorTest(StudyTestMixin, TestCase):

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_redirects_to_dashboard_when_no_incomplete_consents(self, mock_template):
        url = reverse('consent_workflow', args=[self.study.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('dashboard', response['Location'])

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_routes_to_checkbox_when_text_not_accepted(self, mock_template):
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            consent_text_accepted=False,
            is_complete=False,
            study_participant=self.study_participant,
        )
        url = reverse('consent_workflow', args=[self.study.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'consent-form')

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_routes_to_create_flow_when_no_sources_exist(self, mock_template):
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            consent_text_accepted=True,
            is_complete=False,
            study_participant=self.study_participant,
        )
        url = reverse('consent_workflow', args=[self.study.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/data-sources/add/', response['Location'])

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_routes_to_select_view_when_sources_exist(self, mock_template):
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            consent_text_accepted=True,
            is_complete=False,
            study_participant=self.study_participant,
        )
        AwareDataSource.objects.create(
            profile=self.profile,
            name='Existing Source',
            status='active',
        )
        url = reverse('consent_workflow', args=[self.study.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_consent_id_param_targets_specific_consent(self, mock_template):
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            consent_text_accepted=False,
            is_complete=False,
            study_participant=self.study_participant,
        )
        second_consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            consent_text_accepted=False,
            is_complete=False,
            study_participant=self.study_participant,
        )
        url = reverse('consent_workflow', args=[self.study.id])
        response = self.client.get(url, {'consent_id': second_consent.id})
        self.assertEqual(response.status_code, 200)

    @patch('studies.services.get_consent_template', return_value=MOCK_CONSENT_TEMPLATE)
    def test_optional_only_consents_redirect_to_dashboard(self, mock_template):
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='JsonUrlDataSource',
            consent_text_accepted=False,
            is_complete=False,
            is_optional=True,
            study_participant=self.study_participant,
        )
        url = reverse('consent_workflow', args=[self.study.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('dashboard', response['Location'])


# ---------------------------------------------------------------------------
# 8. WithdrawFromStudyViewTest
# ---------------------------------------------------------------------------

class WithdrawFromStudyViewTest(StudyTestMixin, TestCase):

    def setUp(self):
        super().setUp()
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
            source_type='AwareDataSource',
            data_source=self.source1,
            is_complete=True,
            consent_date=timezone.now(),
            study_participant=self.study_participant,
        )
        self.consent2 = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            data_source=self.source2,
            is_complete=True,
            consent_date=timezone.now(),
            study_participant=self.study_participant,
        )

    def test_get_renders_confirmation(self):
        url = reverse('withdraw_from_study', args=[self.study.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_post_revokes_all_active_consents(self):
        url = reverse('withdraw_from_study', args=[self.study.id])
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
        revoked_consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            is_complete=False,
            revocation_date=revoked_time,
            study_participant=self.study_participant,
        )
        url = reverse('withdraw_from_study', args=[self.study.id])
        self.client.post(url)
        revoked_consent.refresh_from_db()
        # The revocation_date should still be the original, not updated
        self.assertEqual(
            revoked_consent.revocation_date.replace(microsecond=0),
            revoked_time.replace(microsecond=0),
        )

    def test_post_redirects_to_dashboard(self):
        url = reverse('withdraw_from_study', args=[self.study.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('dashboard', response['Location'])

    def test_requires_login(self):
        self.client.logout()
        url = reverse('withdraw_from_study', args=[self.study.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])


# ---------------------------------------------------------------------------
# 9. RevokeConsentViewTest
# ---------------------------------------------------------------------------

class RevokeConsentViewTest(StudyTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Revoke Source',
            status='active',
        )
        self.consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
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
            source_type='AwareDataSource',
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
        url = reverse('study_detail', args=[self.study.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    @patch('studies.services.get_study_page_html', return_value=MOCK_STUDY_PAGE_HTML)
    def test_user_in_study_true_with_active_consent(self, mock_page):
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            is_complete=True,
            consent_date=timezone.now(),
            study_participant=self.study_participant,
        )
        url = reverse('study_detail', args=[self.study.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.study.title)

    @patch('studies.services.get_study_page_html', return_value=MOCK_STUDY_PAGE_HTML)
    def test_user_in_study_false_with_revoked_consent(self, mock_page):
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            is_complete=False,
            revocation_date=timezone.now(),
            study_participant=self.study_participant,
        )
        url = reverse('study_detail', args=[self.study.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    @patch('studies.services.get_study_page_html', return_value=MOCK_STUDY_PAGE_HTML)
    def test_nonexistent_study_404(self, mock_page):
        url = reverse('study_detail', args=[99999])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# 11. GetNextConsentHelperTest
# ---------------------------------------------------------------------------

class GetNextConsentHelperTest(StudyTestMixin, TestCase):

    def test_returns_specific_consent_by_id(self):
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            is_complete=False,
            is_optional=False,
            study_participant=self.study_participant,
        )
        result = get_next_consent(self.profile, self.study, consent_id=consent.id)
        self.assertEqual(result, consent)

    def test_404_for_nonexistent_id(self):
        self.assertRaises(Http404, get_next_consent, self.profile, self.study, consent_id=99999)

    def test_returns_first_incomplete_required(self):
        consent1 = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            is_complete=False,
            is_optional=False,
            study_participant=self.study_participant,
        )
        consent2 = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            is_complete=False,
            is_optional=False,
            study_participant=self.study_participant,
        )
        result = get_next_consent(self.profile, self.study)
        self.assertIsNotNone(result)
        self.assertIn(result, [consent1, consent2])

    def test_returns_none_when_all_complete(self):
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            is_complete=True,
            is_optional=False,
            consent_date=timezone.now(),
            study_participant=self.study_participant,
        )
        result = get_next_consent(self.profile, self.study)
        self.assertIsNone(result)

    def test_ignores_optional_consents(self):
        Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='JsonUrlDataSource',
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
            source_type='AwareDataSource',
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

    @patch.object(AwareDataSource, 'fetch_data', return_value=[{'timestamp': 123, 'value': 42}])
    @patch.object(AwareDataSource, 'get_data_types', return_value=['battery'])
    def test_data_rows_include_participant_id(self, mock_types, mock_fetch):
        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='API Test Source',
            status='active',
        )
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            data_source=source,
            is_complete=True,
            consent_date=timezone.now(),
            study_participant=self.study_participant,
        )
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        response = self.client.get(url, {'data_type': 'battery'})
        data = response.json()
        self.assertGreater(data['data_count'], 0)
        for row in data['data']:
            self.assertEqual(row['participant_id'], str(self.study_participant.pseudo_id))

    @patch.object(AwareDataSource, 'fetch_data', return_value=[{'timestamp': 123, 'value': 42}])
    @patch.object(AwareDataSource, 'get_data_types', return_value=['battery'])
    def test_config_data_start_limits_fetched_data(self, mock_types, mock_fetch):
        # Config data_start (June 1) is later than consent_date (Jan 1), so fetch should use June 1
        StudySourceConfiguration.objects.update_or_create(
            study=self.study,
            source_type='AwareDataSource',
            defaults={'status': 'required', 'data_start': timezone.make_aware(datetime(2024, 6, 1))},
        )

        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Config Start Test Source',
            status='active',
        )
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            data_source=source,
            is_complete=True,
            consent_date=timezone.make_aware(datetime(2024, 1, 1)),
            study_participant=self.study_participant,
        )
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        self.client.get(url, {'data_type': 'battery'})

        _, kwargs = mock_fetch.call_args
        self.assertEqual(kwargs['start_date'].date(), datetime(2024, 6, 1).date())

    @patch.object(AwareDataSource, 'fetch_data', return_value=[{'timestamp': 123, 'value': 42}])
    @patch.object(AwareDataSource, 'get_data_types', return_value=['battery'])
    def test_config_data_end_limits_fetched_data(self, mock_types, mock_fetch):
        # Config data_end (Sep 1) should cap the end date passed to fetch_data
        StudySourceConfiguration.objects.update_or_create(
            study=self.study,
            source_type='AwareDataSource',
            defaults={'status': 'required', 'data_end': timezone.make_aware(datetime(2024, 9, 1))},
        )

        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Config End Test Source',
            status='active',
        )
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            data_source=source,
            is_complete=True,
            consent_date=timezone.make_aware(datetime(2024, 1, 1)),
            study_participant=self.study_participant,
        )
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        self.client.get(url, {'data_type': 'battery'})

        _, kwargs = mock_fetch.call_args
        self.assertEqual(kwargs['end_date'].date(), datetime(2024, 9, 1).date())

    @patch.object(AwareDataSource, 'fetch_data', return_value=[{'timestamp': 123, 'value': 42}])
    @patch.object(AwareDataSource, 'get_data_types', return_value=['battery'])
    def test_config_dates_override_when_changed(self, mock_types, mock_fetch):
        # Changing the config's data_start should be reflected in subsequent API calls
        StudySourceConfiguration.objects.update_or_create(
            study=self.study,
            source_type='AwareDataSource',
            defaults={'status': 'required', 'data_start': timezone.make_aware(datetime(2024, 6, 1))},
        )

        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Config Change Test Source',
            status='active',
        )
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            data_source=source,
            is_complete=True,
            consent_date=timezone.make_aware(datetime(2024, 1, 1)),
            study_participant=self.study_participant,
        )
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')

        self.client.get(url, {'data_type': 'battery'})
        _, kwargs = mock_fetch.call_args
        self.assertEqual(kwargs['start_date'].date(), datetime(2024, 6, 1).date())

        StudySourceConfiguration.objects.update_or_create(
            study=self.study,
            source_type='AwareDataSource',
            defaults={'status': 'required', 'data_start': timezone.make_aware(datetime(2024, 8, 1))},
        )

        self.client.get(url, {'data_type': 'battery'})
        _, kwargs = mock_fetch.call_args
        self.assertEqual(kwargs['start_date'].date(), datetime(2024, 8, 1).date())

    @patch.object(AwareDataSource, 'fetch_data', return_value=[{'timestamp': 123, 'value': 42}])
    @patch.object(AwareDataSource, 'get_data_types', return_value=['battery'])
    def test_query_param_narrows_config_window(self, mock_types, mock_fetch):
        # Query params (March 1 - June 1) are narrower than config (Jan 1 - Dec 31),
        # so fetch_data should receive the narrower query-param window
        StudySourceConfiguration.objects.update_or_create(
            study=self.study,
            source_type='AwareDataSource',
            defaults={
                'status': 'required',
                'data_start': timezone.make_aware(datetime(2024, 1, 1)),
                'data_end': timezone.make_aware(datetime(2024, 12, 31)),
            },
        )

        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='Query Param Narrow Test Source',
            status='active',
        )
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            data_source=source,
            is_complete=True,
            consent_date=timezone.make_aware(datetime(2024, 1, 1)),
            study_participant=self.study_participant,
        )
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        self.client.get(url, {'data_type': 'battery', 'start_date': '2024-03-01', 'end_date': '2024-06-01'})

        _, kwargs = mock_fetch.call_args
        self.assertEqual(kwargs['start_date'].date(), datetime(2024, 3, 1).date())
        self.assertEqual(kwargs['end_date'].date(), datetime(2024, 6, 1).date())

    @patch.object(AwareDataSource, 'fetch_data', return_value=[{'timestamp': 123, 'value': 42}])
    @patch.object(AwareDataSource, 'get_data_types', return_value=['battery'])
    def test_no_config_dates_falls_back_to_consent(self, mock_types, mock_fetch):
        # Without source_configurations, fetch_data should receive the consent's data_start
        source = AwareDataSource.objects.create(
            profile=self.profile,
            name='No Config Fallback Test Source',
            status='active',
        )
        consent_start = timezone.make_aware(datetime(2024, 1, 1))
        consent = Consent.objects.create(
            participant=self.profile,
            study=self.study,
            source_type='AwareDataSource',
            data_source=source,
            is_complete=True,
            consent_date=consent_start,
            data_start=consent_start,
            study_participant=self.study_participant,
        )
        self.client.login(username='researcher', password='testpass')
        url = reverse('study_data_api')
        self.client.get(url, {'data_type': 'battery'})

        _, kwargs = mock_fetch.call_args
        self.assertEqual(kwargs['start_date'].date(), datetime(2024, 1, 1).date())


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
            'source_config_entries-TOTAL_FORMS': '0',
            'source_config_entries-INITIAL_FORMS': '0',
            'source_config_entries-MIN_NUM_FORMS': '0',
            'source_config_entries-MAX_NUM_FORMS': '1000',
            'consents-TOTAL_FORMS': '0',
            'consents-INITIAL_FORMS': '0',
            'consents-MIN_NUM_FORMS': '0',
            'consents-MAX_NUM_FORMS': '1000',
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

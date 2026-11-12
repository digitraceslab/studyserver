from django.urls import path
from . import views

urlpatterns = [
    path('', views.study_detail, name='study_detail'),
    path('withdraw/', views.withdraw_from_study, name='withdraw_from_study'),
    path('join/', views.join_study, name='join_study'),
    path('consent/', views.consent_workflow, name='consent_workflow'),
    path('revoke/<int:consent_id>/', views.revoke_consent, name='revoke_consent'),
    path('terms/', views.terms_of_service, name='terms_of_service'),
    path('privacy/', views.privacy_statement, name='privacy_statement'),
    path('terms/version/<int:pk>/', views.terms_of_service_version, name='terms_of_service_version'),
    path('privacy/version/<int:pk>/', views.privacy_statement_version, name='privacy_statement_version'),
    path('info-sheet/', views.info_sheet, name='info_sheet'),
    path('info-sheet/version/<int:pk>/', views.info_sheet_version, name='info_sheet_version'),
    path('api/data', views.study_data_api, name='study_data_api'),
    path('api/data/', views.study_data_api),
    path('api/data/mark-deletable', views.mark_data_deletable, name='mark_data_deletable'),
    path('api/data/mark-deletable/', views.mark_data_deletable),
    path('api/data/unmark-deletable', views.unmark_data_deletable, name='unmark_data_deletable'),
    path('api/data/unmark-deletable/', views.unmark_data_deletable),
    path('api/participants', views.study_participants_api, name='study_participants_api'),
    path('api/participants/', views.study_participants_api),
]

from django.urls import path
from . import views

urlpatterns = [
    path('', views.study_detail, name='study_detail'),
    path('withdraw/', views.withdraw_from_study, name='withdraw_from_study'),
    path('join/', views.join_study, name='join_study'),
    path('consent/', views.consent_workflow, name='consent_workflow'),
    path('revoke/<int:consent_id>/', views.revoke_consent, name='revoke_consent'),
    path('api/data', views.study_data_api, name='study_data_api'),
    path('api/data/', views.study_data_api),
    path('api/data/mark-deletable', views.mark_data_deletable, name='mark_data_deletable'),
    path('api/data/mark-deletable/', views.mark_data_deletable),
]

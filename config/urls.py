from django.urls import path, include
from django.contrib.auth import views as auth_views
from tracker import views
from tracker import api

handler400 = views.bad_request
handler403 = views.forbidden
handler404 = views.not_found
handler500 = views.server_error

urlpatterns = [
    path('api/v1/', include('tracker.api_urls')),
    path('health/live/', api.live),
    path('health/ready/', api.ready),
    path('system/', views.system_status, name='system_status'),
    path('login/', views.ThrottledLoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', views.dashboard, name='dashboard'),
    path('orders/', views.orders, name='orders'),
    path('orders/new/', views.order_new, name='order_new'),
    path('orders/<int:pk>/', views.order_detail, name='order_detail'),
    path('orders/<int:pk>/lines/template/', views.line_template, name='line_template'),
    path('orders/<int:pk>/lines/new/', views.line_new, name='line_new'),
    path('orders/<int:pk>/transactions/<str:kind>/new/', views.transaction_new, name='transaction_new'),
    path('transactions/<int:pk>/correct/', views.transaction_correct, name='transaction_correct'),
    path('shipments/<int:pk>/edit/', views.shipment_edit, name='shipment_edit'),
    path('transactions/<int:pk>/allocate/', views.allocation_new, name='allocation_new'),
    path('transactions/<int:pk>/attachment/', views.attachment, name='attachment'),
    path('finance/', views.finance, name='finance'),
    path('shipments/', views.shipments, name='shipments'),
    path('orders/<int:pk>/shipments/new/', views.shipment_new, name='shipment_new'),
    path('archives/', views.archives, name='archives'),
    path('orders/<int:pk>/export/', views.export_order, name='export_order'),
    path('orders/<int:pk>/lock/', views.lock_order, name='lock_order'),
    path('archives/<int:pk>/download/', views.download_archive, name='download_archive'),
    path('team/', views.team, name='team'),
]

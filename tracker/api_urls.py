from django.urls import path
from . import api

urlpatterns = [
    path('transactions/<int:pk>/corrections/', api.transaction_correction),
    path('shipments/<int:pk>/logistics/', api.shipment_logistics),
    path('shipments/<int:pk>/freight/', api.shipment_freight),
    path('auth/session/', api.session),
    path('auth/login/', api.sign_in),
    path('auth/logout/', api.sign_out),
    path('orders/', api.orders),
    path('orders/<int:pk>/', api.order_detail),
    path('orders/<int:pk>/lines/', api.lines),
    path('orders/<int:pk>/transactions/', api.transactions),
    path('transactions/<int:pk>/allocations/', api.allocations),
    path('transactions/<int:pk>/attachment/', api.attachment),
    path('orders/<int:pk>/shipments/preview/', api.preview_shipment),
    path('orders/<int:pk>/shipments/', api.shipments),
    path('orders/<int:pk>/lock/', api.lock),
    path('orders/<int:pk>/export/', api.export),
    path('archives/<int:pk>/download/', api.archive_download),
]

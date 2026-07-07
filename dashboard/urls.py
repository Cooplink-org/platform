from django.urls import path

from . import views

urlpatterns = [
    path("summary/", views.dashboard_summary, name="dashboard-summary"),
    path("sales/", views.DashboardSalesList.as_view(), name="dashboard-sales"),
    path("listings/", views.DashboardListingsList.as_view(), name="dashboard-listings"),
    path("earnings-timeseries/", views.dashboard_earnings_timeseries, name="dashboard-earnings-timeseries"),
]

"""Crew Hub URL routes (mounted at /hub/)."""

from django.urls import path

from .views import (
    api_comm_move,
    api_comm_remove,
    api_comm_work_type,
    comm_day,
    comm_month,
    comm_rotations,
    comm_rotations_apply,
    comm_staff,
    duty_day,
    duty_month,
    duty_roster,
    hub_home,
    report_detail,
    report_html,
    report_list,
    report_preview,
    report_refresh,
    report_reopen,
    report_save,
    report_submit,
    report_today,
    vehicle_board,
)

app_name = "crew_hub"

urlpatterns = [
    path("", hub_home, name="hub_home"),
    # Comm Center scheduler
    path("comm/", comm_month, name="comm_month"),
    path("comm/staff/", comm_staff, name="comm_staff"),
    path("comm/rotations/", comm_rotations, name="comm_rotations"),
    path("comm/rotations/apply/", comm_rotations_apply, name="comm_rotations_apply"),
    path(
        "comm/api/assignment/<int:pk>/work-type/",
        api_comm_work_type,
        name="api_comm_work_type",
    ),
    path("comm/api/assignment/<int:pk>/move/", api_comm_move, name="api_comm_move"),
    path(
        "comm/api/assignment/<int:pk>/remove/",
        api_comm_remove,
        name="api_comm_remove",
    ),
    path("comm/<str:date_str>/", comm_day, name="comm_day"),
    # Duty officer scheduler
    path("duty/", duty_month, name="duty_month"),
    path("duty/roster/", duty_roster, name="duty_roster"),
    path("duty/<str:date_str>/", duty_day, name="duty_day"),
    # Vehicle status board
    path("vehicles/", vehicle_board, name="vehicle_board"),
    # AOC Daily Report
    path("report/", report_today, name="report_today"),
    path("reports/", report_list, name="report_list"),
    path("report/<str:date_str>/", report_detail, name="report_detail"),
    path("report/<str:date_str>/save/", report_save, name="report_save"),
    path("report/<str:date_str>/preview/", report_preview, name="report_preview"),
    path("report/<str:date_str>/submit/", report_submit, name="report_submit"),
    path("report/<str:date_str>/reopen/", report_reopen, name="report_reopen"),
    path("report/<str:date_str>/refresh/", report_refresh, name="report_refresh"),
    path("report/<str:date_str>/html/", report_html, name="report_html"),
]

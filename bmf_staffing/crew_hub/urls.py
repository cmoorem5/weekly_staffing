"""Crew Hub URL routes (mounted at /hub/)."""

from django.urls import path

from .views import (
    api_comm_move,
    api_comm_remove,
    api_comm_reseat,
    api_comm_work_type,
    api_duty_move,
    api_duty_remove,
    api_duty_work_type,
    calendar_feed,
    calendar_feed_reset,
    comm_day,
    comm_month,
    comm_rotations,
    comm_rotations_apply,
    comm_staff,
    duty_day,
    duty_month,
    duty_roster,
    duty_rotations,
    duty_rotations_apply,
    hours_report,
    hours_report_csv,
    hub_home,
    my_schedule,
    notifications,
    notifications_read,
    report_detail,
    report_html,
    report_list,
    report_preview,
    report_refresh,
    report_reopen,
    report_save,
    report_submit,
    report_today,
    time_off_decide,
    time_off_manage,
    time_off_submit,
    user_admin,
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
        "comm/api/assignment/<int:pk>/reseat/",
        api_comm_reseat,
        name="api_comm_reseat",
    ),
    path(
        "comm/api/assignment/<int:pk>/remove/",
        api_comm_remove,
        name="api_comm_remove",
    ),
    path("comm/<str:date_str>/", comm_day, name="comm_day"),
    # Duty officer scheduler
    path("duty/", duty_month, name="duty_month"),
    path("duty/roster/", duty_roster, name="duty_roster"),
    path("duty/rotations/", duty_rotations, name="duty_rotations"),
    path("duty/rotations/apply/", duty_rotations_apply, name="duty_rotations_apply"),
    path(
        "duty/api/assignment/<int:pk>/work-type/",
        api_duty_work_type,
        name="api_duty_work_type",
    ),
    path("duty/api/assignment/<int:pk>/move/", api_duty_move, name="api_duty_move"),
    path(
        "duty/api/assignment/<int:pk>/remove/",
        api_duty_remove,
        name="api_duty_remove",
    ),
    # Hours / payroll reporting
    path("reports/hours/", hours_report, name="hours_report"),
    path("reports/hours/csv/", hours_report_csv, name="hours_report_csv"),
    # Self-service: my schedule, time off, notifications
    path("me/", my_schedule, name="my_schedule"),
    path("me/time-off/", time_off_submit, name="time_off_submit"),
    # Personal iCal feed (token-authenticated for calendar apps) + reset
    path("calendar/<str:token>/feed.ics", calendar_feed, name="calendar_feed"),
    path("me/calendar/reset/", calendar_feed_reset, name="calendar_feed_reset"),
    path("timeoff/", time_off_manage, name="time_off_manage"),
    path("timeoff/<int:pk>/decide/", time_off_decide, name="time_off_decide"),
    path("notifications/", notifications, name="notifications"),
    # Users & permission levels
    path("users/", user_admin, name="user_admin"),
    path("notifications/read/", notifications_read, name="notifications_read"),
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

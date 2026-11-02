"""Base weather strip: plain-language decoding, categories, caching."""

import datetime as dt
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crew_hub import weather

SAMPLE = {
    "icaoId": "KBED",
    "reportTime": "2026-07-07 14:52:00",
    "temp": 4.0,
    "wdir": 40,
    "wspd": 12,
    "wgst": 20,
    "visib": "2",
    "wxString": "-RA BR",
    "clouds": [{"cover": "SCT", "base": 500}, {"cover": "BKN", "base": 800}],
    "rawOb": "KBED 071452Z 04012G20KT 2SM -RA BR SCT005 BKN008 04/03 A2992",
}


class DecodeTests(TestCase):
    def test_plain_language_summary(self):
        decoded = weather.decode_metar(SAMPLE)
        self.assertEqual(decoded["category"], "IFR")
        self.assertEqual(decoded["meaning"], "instrument conditions")
        self.assertEqual(
            decoded["summary"],
            "Light rain, mist, broken clouds at 800 ft, 2 mi visibility, "
            "northeast wind 14 mph gusting 23, 39°F",
        )
        self.assertEqual(decoded["observed"], "14:52")

    def test_clear_day_is_vfr(self):
        decoded = weather.decode_metar(
            {
                "icaoId": "KPYM",
                "temp": 22.0,
                "wdir": 270,
                "wspd": 8,
                "visib": "10+",
                "clouds": [{"cover": "CLR", "base": None}],
            }
        )
        self.assertEqual(decoded["category"], "VFR")
        self.assertIn("Clear skies", decoded["summary"])
        self.assertIn("10+ mi visibility", decoded["summary"])
        self.assertIn("west wind 9 mph", decoded["summary"])
        self.assertIn("72°F", decoded["summary"])

    def test_flight_category_boundaries(self):
        cases = [
            ((6.0, 3100), "VFR"),
            ((4.0, None), "MVFR"),
            ((None, 2500), "MVFR"),
            ((2.0, None), "IFR"),
            ((None, 800), "IFR"),
            ((0.5, None), "LIFR"),
            ((None, 300), "LIFR"),
            ((None, None), "N/A"),
        ]
        for (vis, ceiling), expected in cases:
            self.assertEqual(
                weather.flight_category(vis, ceiling), expected, (vis, ceiling)
            )


@override_settings(CREW_HUB_WEATHER_STATIONS=[("Bedford", "KBED")])
class BaseWeatherTests(TestCase):
    def setUp(self):
        weather._cache.clear()

    def test_rows_per_base_and_caching(self):
        with patch.object(
            weather, "_fetch_metars", return_value={"KBED": SAMPLE}
        ) as fetch:
            rows = weather.get_base_weather()
            weather.get_base_weather()  # second call hits the cache
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(rows[0]["base"], "Bedford")
        self.assertEqual(rows[0]["category"], "IFR")

    def test_fetch_failure_degrades_gracefully(self):
        with patch.object(weather, "_fetch_metars", side_effect=OSError("down")):
            rows = weather.get_base_weather()
        self.assertEqual(rows[0]["category"], "N/A")
        self.assertIn("unavailable", rows[0]["summary"])

    def test_cold_cache_page_does_not_wait_on_the_api(self):
        User.objects.create_user("staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        with patch.object(weather, "_fetch_metars") as fetch:
            response = self.client.get(reverse("crew_hub:hub_home"))
        fetch.assert_not_called()
        self.assertContains(response, "Loading weather")
        self.assertContains(response, reverse("crew_hub:hub_weather"))

    def test_weather_fragment_endpoint_returns_cards(self):
        User.objects.create_user("staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        with patch.object(weather, "_fetch_metars", return_value={"KBED": SAMPLE}):
            response = self.client.get(reverse("crew_hub:hub_weather"))
        self.assertContains(response, "Light rain, mist")
        self.assertNotContains(response, "Loading weather")

    def test_weather_fragment_requires_login(self):
        response = self.client.get(reverse("crew_hub:hub_weather"))
        self.assertEqual(response.status_code, 302)

    def test_concurrent_requests_share_one_fetch(self):
        import threading

        calls = []

        def slow_fetch(_stations):
            calls.append(1)
            import time

            time.sleep(0.2)
            return {"KBED": SAMPLE}

        with patch.object(weather, "_fetch_metars", side_effect=slow_fetch):
            threads = [
                threading.Thread(target=weather.get_base_weather) for _ in range(4)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(calls), 1)

    def test_today_board_shows_weather_strip(self):
        User.objects.create_user("staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        weather._cache.clear()
        with patch.object(weather, "_fetch_metars", return_value={"KBED": SAMPLE}):
            weather.get_base_weather()  # warm cache, as the startup prefetch does
            response = self.client.get(reverse("crew_hub:hub_home"))
        self.assertContains(response, "Base weather")
        self.assertContains(response, "Light rain, mist")
        self.assertContains(response, "IFR")
        self.assertContains(response, "not an official weather briefing")

    def test_todays_aoc_report_shows_weather_strip(self):
        User.objects.create_user("staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        today = timezone.localdate()
        with patch.object(weather, "_fetch_metars", return_value={"KBED": SAMPLE}):
            weather.get_base_weather()
            response = self.client.get(
                reverse(
                    "crew_hub:report_detail", kwargs={"date_str": today.isoformat()}
                )
            )
        self.assertContains(response, "Base weather")
        self.assertContains(response, "Light rain, mist")

    def test_past_aoc_report_has_no_weather_strip(self):
        User.objects.create_user("staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        yesterday = timezone.localdate() - dt.timedelta(days=1)
        with patch.object(weather, "_fetch_metars", return_value={"KBED": SAMPLE}):
            response = self.client.get(
                reverse(
                    "crew_hub:report_detail",
                    kwargs={"date_str": yesterday.isoformat()},
                )
            )
        self.assertNotContains(response, "Base weather")


class DisabledWeatherTests(TestCase):
    @override_settings(CREW_HUB_WEATHER_STATIONS=[])
    def test_no_stations_means_no_strip(self):
        self.assertEqual(weather.get_base_weather(), [])
        User.objects.create_user("staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        response = self.client.get(reverse("crew_hub:hub_home"))
        self.assertNotContains(response, "Base weather")

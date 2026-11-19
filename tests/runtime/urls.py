"""Tests-only urlconf: async views that wait on heavy work without holding a thread."""

import time

from django.http import JsonResponse
from django.urls import path

from apps.kernel import runtime


async def slow(request):
    await runtime.run_heavy(time.sleep, 1.0)
    return JsonResponse({"ok": True})


async def fast(request):
    return JsonResponse({"ok": True})


urlpatterns = [path("slow", slow), path("fast", fast)]

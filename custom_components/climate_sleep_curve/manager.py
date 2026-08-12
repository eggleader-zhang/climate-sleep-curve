"""Profiles, controllers, sessions, and scheduling manager."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    EVENT_POINT_PROCESSED,
    EVENT_SESSION_COMPLETED,
    EVENT_SESSION_STARTED,
    EVENT_SESSION_STOPPED,
    MAX_DURATION_MINUTES,
    MAX_POINTS,
    MIN_DURATION_MINUTES,
    SIGNAL_CONTROLLER_ADDED,
    SIGNAL_UPDATED,
    STORE_VERSION,
)
from .executor import async_execute_temperatures
from .models import RevisionConflict, ValidationError, make_session, new_id, parse_utc, utcnow_iso, validate_controller, validate_profile
from .storage import CurveStorage

class ClimateSleepCurveManager:
    """Own all integration state and one-shot callbacks."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.storage = CurveStorage(hass)
        self.data: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._data_lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()
        self._session_cancels: dict[str, list] = {}
        self._schedule_cancels: dict[str, Any] = {}
        self._last_scheduled_date: dict[str, str] = {}
        self._tasks: set[asyncio.Task] = set()
        self._cancel_requests: set[str] = set()
        self._unloading = False

    @property
    def profiles(self) -> dict[str, dict[str, Any]]:
        return self.data["profiles"]

    @property
    def controllers(self) -> dict[str, dict[str, Any]]:
        return self.data["controllers"]

    @property
    def sessions(self) -> dict[str, dict[str, Any]]:
        return self.data["sessions"]

    async def async_setup(self, settings: dict[str, Any] | None = None) -> None:
        self._unloading = False
        self.data = await self.storage.async_load()
        changed = False
        if settings:
            updated_settings = {**self.data["settings"], **settings}
            changed = updated_settings != self.data["settings"]
            self.data["settings"] = updated_settings
        changed = self._prune_history() or changed
        await self._async_restore_sessions()
        if changed:
            await self._save()
        self._reschedule_automatic_starts()

    async def async_unload(self) -> None:
        self._unloading = True
        for callbacks in self._session_cancels.values():
            for cancel in callbacks:
                cancel()
        for cancel in self._schedule_cancels.values():
            cancel()
        self._session_cancels.clear()
        self._schedule_cancels.clear()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        async with self._data_lock:
            await self.storage.async_save(self.data)

    async def async_reload(self) -> None:
        await self.async_unload()
        self.data = await self.storage.async_load()
        await self._async_restore_sessions()
        self._reschedule_automatic_starts()
        self._unloading = False
        self._notify()

    async def _save(self) -> None:
        async with self._save_lock:
            await self.storage.async_save(self.data)

    @asynccontextmanager
    async def _transaction(self):
        """Serialize mutations and restore memory when persistence fails."""
        async with self._data_lock:
            before = deepcopy(self.data)
            try:
                yield
                await self.storage.async_save(self.data)
            except Exception:
                self.data = before
                raise

    def _create_task(self, coro) -> None:
        """Track callback work so unload cannot leave device work behind."""
        if self._unloading:
            coro.close()
            return
        task = self.hass.async_create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _notify(self, controller_id: str | None = None) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATED, controller_id)

    def get_state(self) -> dict[str, Any]:
        return {
            "schema_version": STORE_VERSION,
            "profiles": list(deepcopy(self.profiles).values()),
            "controllers": list(deepcopy(self.controllers).values()),
            "active_sessions": [deepcopy(item) for item in self.sessions.values() if item["status"] == "running"],
            "capabilities": {
                "min_duration_minutes": MIN_DURATION_MINUTES,
                "max_duration_minutes": MAX_DURATION_MINUTES,
                "max_points": MAX_POINTS,
            },
        }

    async def async_save_profile(self, raw: dict[str, Any], expected_revision: int | None) -> dict[str, Any]:
        normalized = validate_profile(raw)
        profile_id = raw.get("id")
        if profile_id is not None and not isinstance(profile_id, str):
            raise ValidationError("invalid_profile", "Profile id must be a string")
        now = utcnow_iso()
        async with self._transaction():
            if profile_id:
                current = self.profiles.get(profile_id)
                if current is None:
                    raise ValidationError("not_found", "Profile does not exist")
                if expected_revision != current["revision"]:
                    raise RevisionConflict()
                result = {**current, **normalized, "revision": current["revision"] + 1, "updated_at": now}
                event = "profile_updated"
            else:
                profile_id = new_id()
                result = {"id": profile_id, **normalized, "created_at": now, "updated_at": now, "revision": 1}
                event = "profile_created"
            self.profiles[profile_id] = result
        self._notify()
        self._broadcast(event, {"profile": deepcopy(result)})
        return deepcopy(result)

    async def async_delete_profile(self, profile_id: str, expected_revision: int) -> None:
        async with self._transaction():
            current = self.profiles.get(profile_id)
            if current is None:
                raise ValidationError("not_found", "Profile does not exist")
            if current["revision"] != expected_revision:
                raise RevisionConflict()
            references = [item["id"] for item in self.controllers.values() if item["profile_id"] == profile_id]
            if references:
                raise ValidationError("profile_in_use", f"Profile is used by controllers: {', '.join(references)}")
            del self.profiles[profile_id]
        self._notify()
        self._broadcast("profile_deleted", {"profile_id": profile_id})

    async def async_duplicate_profile(self, profile_id: str, name: str) -> dict[str, Any]:
        source = self.profiles.get(profile_id)
        if source is None:
            raise ValidationError("not_found", "Profile does not exist")
        return await self.async_save_profile({**source, "id": None, "name": name}, None)

    async def async_save_controller(self, raw: dict[str, Any], expected_revision: int | None) -> dict[str, Any]:
        controller_data = dict(raw)
        controller_id = raw.get("id")
        if controller_id is not None and not isinstance(controller_id, str):
            raise ValidationError("invalid_controller", "Controller id must be a string")
        current = self.controllers.get(controller_id) if controller_id else None
        if (
            current
            and isinstance(controller_data.get("climate_entity_id"), str)
            and controller_data.get("climate_entity_ids") == current.get("climate_entity_ids")
            and controller_data.get("climate_entity_id") != current.get("climate_entity_id")
        ):
            # Cards before 0.2.0 edit only the singular compatibility field after
            # spreading the controller returned by get_state.
            controller_data["climate_entity_ids"] = [controller_data["climate_entity_id"]]
        controller_data.setdefault("retry_count", self.data["settings"]["default_retry_count"])
        controller_data.setdefault("retry_delay_seconds", self.data["settings"]["default_retry_delay_seconds"])
        normalized = validate_controller(controller_data, set(self.profiles))
        for entity_id in normalized["climate_entity_ids"]:
            state = self.hass.states.get(entity_id)
            if state is None:
                raise ValidationError("invalid_entity", f"The climate entity does not exist: {entity_id}")
            try:
                supported = int(state.attributes.get("supported_features", 0))
            except (TypeError, ValueError):
                supported = 0
            if not (supported & ClimateEntityFeature.TARGET_TEMPERATURE) and "temperature" not in state.attributes:
                raise ValidationError("unsupported_entity", f"The climate entity does not support a target temperature: {entity_id}")
        now = utcnow_iso()
        created = not controller_id
        config_lock = self._locks.setdefault(controller_id or "__new_controller__", asyncio.Lock())
        async with config_lock:
            async with self._transaction():
                if normalized["profile_id"] not in self.profiles:
                    raise ValidationError("not_found", "Profile does not exist")
                if controller_id:
                    current = self.controllers.get(controller_id)
                    if current is None:
                        raise ValidationError("not_found", "Controller does not exist")
                    if expected_revision != current["revision"]:
                        raise RevisionConflict()
                    result = {**current, **normalized, "revision": current["revision"] + 1, "updated_at": now}
                else:
                    controller_id = new_id()
                    result = {"id": controller_id, **normalized, "created_at": now, "updated_at": now, "revision": 1}
                self.controllers[controller_id] = result
            self._schedule_automatic_start(result)
            self._notify(controller_id)
            if created:
                async_dispatcher_send(self.hass, SIGNAL_CONTROLLER_ADDED, controller_id)
            self._broadcast("controller_updated", {"controller": deepcopy(result)})
        return deepcopy(result)

    async def async_delete_controller(self, controller_id: str, expected_revision: int) -> None:
        current = self.controllers.get(controller_id)
        if current is None:
            raise ValidationError("not_found", "Controller does not exist")
        if current["revision"] != expected_revision:
            raise RevisionConflict()
        pending = self.active_session(controller_id)
        if pending:
            self._cancel_requests.add(pending["id"])
        lock = self._locks.setdefault(controller_id, asyncio.Lock())
        try:
            async with lock:
                controller = self.controllers.get(controller_id)
                if not controller:
                    raise ValidationError("not_found", "Controller does not exist")
                if controller["revision"] != expected_revision:
                    raise RevisionConflict()
                async with self._transaction():
                    active = self.active_session(controller_id)
                    if active:
                        active["status"] = "cancelled"
                        active["next_offset_minutes"] = None
                        active["updated_at"] = utcnow_iso()
                    del self.controllers[controller_id]
                if active:
                    self._after_session_finished(active, EVENT_SESSION_STOPPED)
        finally:
            if pending:
                self._cancel_requests.discard(pending["id"])
        cancel = self._schedule_cancels.pop(controller_id, None)
        if cancel:
            cancel()
        self._notify(controller_id)
        self._broadcast("controller_updated", {"controller_id": controller_id, "deleted": True})

    def active_session(self, controller_id: str) -> dict[str, Any] | None:
        return next((item for item in self.sessions.values() if item["controller_id"] == controller_id and item["status"] == "running"), None)

    async def async_start_session(self, controller_id: str, profile_id: str | None = None, replace: bool = False, source: str = "manual") -> dict[str, Any]:
        current = self.controllers.get(controller_id)
        if current is None:
            raise ValidationError("not_found", "Controller does not exist")
        if profile_id is not None and profile_id not in self.profiles:
            raise ValidationError("not_found", "Profile does not exist")
        pending = self.active_session(controller_id)
        if pending and replace:
            self._cancel_requests.add(pending["id"])
        lock = self._locks.setdefault(controller_id, asyncio.Lock())
        try:
            async with lock:
                controller = self.controllers.get(controller_id)
                if controller is None:
                    raise ValidationError("not_found", "Controller does not exist")
                active = self.active_session(controller_id)
                if active and not replace:
                    raise ValidationError("session_already_running", "A session is already running")
                selected = self.profiles.get(profile_id or controller["profile_id"])
                if selected is None:
                    raise ValidationError("not_found", "Profile does not exist")
                session = make_session(controller, selected, source, dt_util.utcnow())
                async with self._transaction():
                    if active:
                        active["status"] = "replaced"
                        active["next_offset_minutes"] = None
                        active["updated_at"] = utcnow_iso()
                    self.sessions[session["id"]] = session
                if active:
                    self._after_session_finished(active, EVENT_SESSION_STOPPED)
                self._schedule_session(session)
                self.hass.bus.async_fire(EVENT_SESSION_STARTED, self._event_data(session))
                self._broadcast("session_started", {"session": deepcopy(session)})
                self._notify(controller_id)
        finally:
            if pending:
                self._cancel_requests.discard(pending["id"])
        await self.async_process_point(session["id"], 0)
        return deepcopy(session)

    async def async_stop_session(self, controller_id: str, allow_missing: bool = False) -> None:
        pending = self.active_session(controller_id)
        if pending:
            self._cancel_requests.add(pending["id"])
        lock = self._locks.setdefault(controller_id, asyncio.Lock())
        try:
            async with lock:
                session = self.active_session(controller_id)
                if session is None:
                    if allow_missing:
                        return
                    raise ValidationError("session_not_running", "No session is running")
                await self._async_finish_locked(session, "cancelled", EVENT_SESSION_STOPPED)
        finally:
            if pending:
                self._cancel_requests.discard(pending["id"])

    async def async_restart_session(self, controller_id: str) -> dict[str, Any]:
        return await self.async_start_session(controller_id, replace=True)

    async def async_apply_current_point(self, controller_id: str) -> dict[str, Any]:
        lock = self._locks.setdefault(controller_id, asyncio.Lock())
        async with lock:
            session = self.active_session(controller_id)
            if session is None:
                raise ValidationError("session_not_running", "No session is running")
            elapsed = (dt_util.utcnow() - parse_utc(session["started_at"])).total_seconds() / 60
            points = [p for p in session["profile_snapshot"]["points"] if p["offset_minutes"] <= elapsed]
            if not points:
                raise ValidationError("not_found", "No current point")
            return await self._execute(session, points[-1], record=False)

    async def async_process_point(self, session_id: str, offset_minutes: int) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            return
        lock = self._locks.setdefault(session["controller_id"], asyncio.Lock())
        async with lock:
            if session["status"] != "running" or any(p["offset_minutes"] == offset_minutes for p in session["processed_points"]):
                return
            point = next((p for p in session["profile_snapshot"]["points"] if p["offset_minutes"] == offset_minutes), None)
            if point is None:
                return
            await self._execute(session, point, record=True)

    async def _execute(self, session: dict[str, Any], point: dict[str, Any], record: bool) -> dict[str, Any]:
        controller = self.controllers.get(session["controller_id"], {})
        entity_ids = list(session.get("climate_entity_ids") or [session["climate_entity_id"]])
        result = await async_execute_temperatures(
            self.hass,
            entity_ids,
            point["temperature"],
            int(controller.get("retry_count", 1)),
            int(controller.get("retry_delay_seconds", 10)),
            lambda: (
                not self._unloading
                and session["status"] == "running"
                and session["id"] not in self._cancel_requests
            ),
        )
        if record and session["status"] == "running" and session["id"] not in self._cancel_requests:
            processed = {
                "offset_minutes": point["offset_minutes"],
                "scheduled_at": (parse_utc(session["started_at"]) + timedelta(minutes=point["offset_minutes"])).isoformat().replace("+00:00", "Z"),
                "processed_at": utcnow_iso(),
                "target_temperature": point["temperature"],
                **result,
            }
            async with self._transaction():
                session["processed_points"].append(processed)
                session["last_result"] = result["result"]
                session["last_error"] = result.get("error")
                session["last_entity_results"] = result["entity_results"]
                session["updated_at"] = utcnow_iso()
                processed_offsets = {item["offset_minutes"] for item in session["processed_points"]}
                upcoming = [p for p in session["profile_snapshot"]["points"] if p["offset_minutes"] not in processed_offsets]
                session["next_offset_minutes"] = upcoming[0]["offset_minutes"] if upcoming else None
            event = {**self._event_data(session), **processed}
            self.hass.bus.async_fire(EVENT_POINT_PROCESSED, event)
            self._broadcast("point_processed", event)
            self._notify(session["controller_id"])
        return result

    async def _async_finish(self, session: dict[str, Any], status: str, event_name: str = EVENT_SESSION_COMPLETED) -> None:
        lock = self._locks.setdefault(session["controller_id"], asyncio.Lock())
        async with lock:
            await self._async_finish_locked(session, status, event_name)

    async def _async_finish_locked(self, session: dict[str, Any], status: str, event_name: str) -> None:
        """Finish a session while its controller lock is held."""
        if session["status"] != "running":
            return
        async with self._transaction():
            session["status"] = status
            session["next_offset_minutes"] = None
            session["updated_at"] = utcnow_iso()
        self._after_session_finished(session, event_name)

    def _after_session_finished(self, session: dict[str, Any], event_name: str) -> None:
        """Cancel callbacks and publish an already-persisted session finish."""
        for cancel in self._session_cancels.pop(session["id"], []):
            cancel()
        data = self._event_data(session)
        self.hass.bus.async_fire(event_name, data)
        self._broadcast("session_completed" if session["status"].startswith("completed") else "session_stopped", data)
        self._notify(session["controller_id"])

    def _schedule_session(self, session: dict[str, Any]) -> None:
        for cancel in self._session_cancels.pop(session["id"], []):
            cancel()
        callbacks = []
        now = dt_util.utcnow()
        started = parse_utc(session["started_at"])
        processed = {p["offset_minutes"] for p in session["processed_points"]}
        for point in session["profile_snapshot"]["points"]:
            when = started + timedelta(minutes=point["offset_minutes"])
            if point["offset_minutes"] in processed or when <= now:
                continue
            callbacks.append(async_track_point_in_utc_time(self.hass, self._point_callback(session["id"], point["offset_minutes"]), when))
        ends_at = parse_utc(session["ends_at"])
        if ends_at > now:
            callbacks.append(async_track_point_in_utc_time(self.hass, self._end_callback(session["id"]), ends_at))
        self._session_cancels[session["id"]] = callbacks

    def _point_callback(self, session_id: str, offset: int):
        @callback
        def run(_now: datetime) -> None:
            self._create_task(self.async_process_point(session_id, offset))
        return run

    def _end_callback(self, session_id: str):
        @callback
        def run(_now: datetime) -> None:
            session = self.sessions.get(session_id)
            if session:
                self._create_task(self._async_finish(session, "completed"))
        return run

    async def _async_restore_sessions(self) -> None:
        now = dt_util.utcnow()
        changed = False
        for session in self.sessions.values():
            if session["status"] != "running":
                continue
            if parse_utc(session["ends_at"]) <= now:
                session["status"] = "completed_after_restart"
                session["next_offset_minutes"] = None
                changed = True
                continue
            started = parse_utc(session["started_at"])
            processed = {p["offset_minutes"] for p in session["processed_points"]}
            for point in session["profile_snapshot"]["points"]:
                if point["offset_minutes"] in processed:
                    continue
                scheduled = started + timedelta(minutes=point["offset_minutes"])
                if scheduled <= now:
                    session["processed_points"].append({
                        "offset_minutes": point["offset_minutes"], "scheduled_at": scheduled.isoformat().replace("+00:00", "Z"),
                        "processed_at": utcnow_iso(), "target_temperature": point["temperature"], "result": "missed_during_restart",
                        "attempts": 0, "error": None,
                    })
                    session["last_result"] = "missed_during_restart"
                    changed = True
            processed = {p["offset_minutes"] for p in session["processed_points"]}
            upcoming = [p for p in session["profile_snapshot"]["points"] if p["offset_minutes"] not in processed]
            session["next_offset_minutes"] = upcoming[0]["offset_minutes"] if upcoming else None
            self._schedule_session(session)
        if changed:
            await self._save()

    def _reschedule_automatic_starts(self) -> None:
        for controller in self.controllers.values():
            self._schedule_automatic_start(controller)

    def _schedule_automatic_start(self, controller: dict[str, Any]) -> None:
        old = self._schedule_cancels.pop(controller["id"], None)
        if old:
            old()
        automatic = controller["automatic_start"]
        if not controller["enabled"] or not automatic["enabled"]:
            return
        hour, minute, second = (int(value) for value in automatic["time"].split(":"))

        @callback
        def start(now: datetime) -> None:
            local = dt_util.as_local(now)
            date_key = local.date().isoformat()
            if local.weekday() not in automatic["weekdays"] or self._last_scheduled_date.get(controller["id"]) == date_key:
                return
            self._last_scheduled_date[controller["id"]] = date_key
            if not self.active_session(controller["id"]):
                self._create_task(self.async_start_session(controller["id"], source="scheduled"))

        self._schedule_cancels[controller["id"]] = async_track_time_change(self.hass, start, hour=hour, minute=minute, second=second)

    def _event_data(self, session: dict[str, Any]) -> dict[str, Any]:
        entity_ids = list(session.get("climate_entity_ids") or [session["climate_entity_id"]])
        return {
            "controller_id": session["controller_id"],
            "session_id": session["id"],
            "climate_entity_ids": entity_ids,
            "climate_entity_id": entity_ids[0],
        }

    def _broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        self.hass.bus.async_fire(f"climate_sleep_curve_internal_{event_type}", data)

    def controller_status(self, controller_id: str) -> dict[str, Any]:
        session = self.active_session(controller_id)
        if not session:
            history = [s for s in self.sessions.values() if s["controller_id"] == controller_id]
            latest = max(history, key=lambda item: item["updated_at"], default=None)
            return {"state": latest["status"] if latest else "idle", "session": latest}
        started, ends = parse_utc(session["started_at"]), parse_utc(session["ends_at"])
        total = max(1, (ends - started).total_seconds())
        progress = min(100, max(0, (dt_util.utcnow() - started).total_seconds() / total * 100))
        next_offset = session["next_offset_minutes"]
        next_point = next((p for p in session["profile_snapshot"]["points"] if p["offset_minutes"] == next_offset), None)
        return {
            "state": "running", "session": session, "progress_percent": round(progress, 1),
            "next_execution_at": (started + timedelta(minutes=next_offset)).isoformat().replace("+00:00", "Z") if next_offset is not None else None,
            "next_target_temperature": next_point["temperature"] if next_point else None,
        }

    def _prune_history(self) -> bool:
        """Remove expired inactive sessions while retaining every active session."""
        retention = int(self.data["settings"].get("history_retention_days", 30))
        cutoff = dt_util.utcnow() - timedelta(days=retention)
        expired = [
            session_id
            for session_id, session in self.sessions.items()
            if session.get("status") != "running" and parse_utc(session["updated_at"]) < cutoff
        ]
        for session_id in expired:
            del self.sessions[session_id]
        return bool(expired)

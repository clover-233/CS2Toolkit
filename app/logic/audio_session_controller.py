import threading
import time


class AudioSessionController:
    def __init__(self, session_provider=None, report=None):
        self._reduced_sessions = {}
        self._session_provider = session_provider
        self._report = report
        self._volume_lock = threading.RLock()
        self._condition = threading.Condition()
        self._desired = None
        self._request_id = self._completed_id = 0
        self._thread = None
        self._closed = False

    def _sessions(self):
        if self._session_provider is not None:
            return self._session_provider()
        from pycaw.pycaw import AudioUtilities
        return AudioUtilities.GetAllSessions()

    @staticmethod
    def _session_key(session):
        process = session.Process
        if process:
            # One process can own several audio sessions with different levels.
            return process.name().lower(), process.pid, session.InstanceIdentifier
        return None

    def reduce_process_volume(self, process_names, target_percent):
        if not process_names:
            return False, "未指定需要控制音量的进程。"
        target_volume = max(0.0, min(float(target_percent) / 100.0, 1.0))
        names = {name.lower() for name in process_names}
        lowered = 0
        with self._volume_lock:
            try:
                for session in self._sessions():
                    key = self._session_key(session)
                    if key is None or key[0] not in names:
                        continue
                    volume = session.SimpleAudioVolume
                    if key not in self._reduced_sessions:
                        self._reduced_sessions[key] = volume.GetMasterVolume()
                    volume.SetMasterVolume(min(self._reduced_sessions[key], target_volume), None)
                    lowered += 1
            except Exception as exc:
                return False, f"调整游戏音量失败：{exc}"
        return bool(lowered), f"已降低 {lowered} 个音频会话的音量。"

    def restore_volume(self):
        restored = 0
        with self._volume_lock:
            if not self._reduced_sessions:
                return True, "没有待恢复的音频会话。"
            try:
                sessions = {self._session_key(session): session for session in self._sessions()}
                for key, original in list(self._reduced_sessions.items()):
                    session = sessions.get(key)
                    if session is None:
                        continue
                    session.SimpleAudioVolume.SetMasterVolume(original, None)
                    # Only discard the original level after a successful write.
                    del self._reduced_sessions[key]
                    restored += 1
            except Exception as exc:
                return False, f"恢复游戏音量失败：{exc}"
            pending = len(self._reduced_sessions)
        return pending == 0, f"已恢复 {restored} 个音频会话的音量，待恢复 {pending} 个。"

    def request_reduction(self, process_names=None, target_percent=100):
        """Coalesce flash changes on one COM worker without blocking the UI."""
        desired = (tuple(process_names), target_percent) if process_names else None
        with self._condition:
            if self._closed:
                return False
            if desired is None and self._thread is None and not self._reduced_sessions:
                return True
            self._desired = desired
            self._request_id += 1
            self._start_worker()
            self._condition.notify_all()
        return True

    def _start_worker(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='CS2Toolkit-FlashAudio', daemon=True)
            self._thread.start()

    def _diagnostic(self, event, **values):
        if self._report is not None:
            try:
                self._report(event, **values)
            except Exception:
                pass

    def _run(self):
        initialized = False
        try:
            import comtypes
            comtypes.CoInitialize()
            initialized = True
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._request_id != self._completed_id or self._closed)
                    if self._closed and self._request_id == self._completed_id:
                        return
                    desired, request_id = self._desired, self._request_id
                for attempt in range(3):
                    started = time.monotonic()
                    ok, message = (self.reduce_process_volume(*desired) if desired is not None else self.restore_volume())
                    self._diagnostic('flash_audio_operation', action='reduce' if desired else 'restore',
                        success=ok, attempt=attempt + 1, pending_sessions=len(self._reduced_sessions),
                        operation_ms=round((time.monotonic() - started) * 1000, 1), detail=message)
                    with self._condition:
                        # A restore arriving during a slow reduction runs next
                        # on this thread, after the reduction has finished.
                        if request_id != self._request_id:
                            break
                        if ok or attempt == 2:
                            self._completed_id = request_id
                            self._condition.notify_all()
                            break
                        self._condition.wait((.1, .3)[attempt])
                        if request_id != self._request_id:
                            break
        except Exception as exc:
            self._diagnostic('flash_audio_error', detail=str(exc))
        finally:
            if initialized:
                comtypes.CoUninitialize()
            with self._condition:
                self._thread = None
                self._completed_id = self._request_id
                self._condition.notify_all()

    def wait_until_idle(self, timeout=2):
        with self._condition:
            return self._condition.wait_for(lambda: self._completed_id == self._request_id, timeout)

    def close(self, timeout=2):
        """Drain an in-flight reduction and restore before shutting down."""
        with self._condition:
            self._closed = True
            if self._thread is None and not self._reduced_sessions:
                return True
            self._desired = None
            self._request_id += 1
            self._start_worker()
            worker = self._thread
            self._condition.notify_all()
        worker.join(timeout)
        return not worker.is_alive() and not self._reduced_sessions

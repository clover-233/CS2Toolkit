"""In-memory pre-flash frames and source-over composition for alpha artwork."""
from collections import deque
import ctypes
from ctypes import wintypes
import threading
import time

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QImage, QImageReader, QPainter


def flash_image_extensions():
    return {'.' + bytes(fmt).decode('ascii').lower()
            for fmt in QImageReader.supportedImageFormats()}


def image_has_transparency(image):
    if image.isNull() or not image.hasAlphaChannel():
        return False
    rgba = image.convertToFormat(QImage.Format_RGBA8888)
    alpha = bytes(rgba.constBits())[3::4]
    return alpha.count(255) != len(alpha)


def compose_flash_image(artwork, background, size, scale_mode='stretch'):
    """Flatten artwork over a frozen scene; transparent artwork stays unmodified."""
    if size.isEmpty():
        return QImage()
    result = QImage(size, QImage.Format_ARGB32_Premultiplied)
    # A missing snapshot gets an explicit dark fallback, never live post-flash
    # gameplay/whiteout masquerading as a pre-flash scene.
    result.fill(Qt.black)
    painter = QPainter(result)
    if background is not None and not background.isNull():
        painter.drawImage(result.rect(), background)
    if not artwork.isNull():
        if scale_mode == 'stretch':
            painter.drawImage(result.rect(), artwork)
        else:
            mode = (Qt.KeepAspectRatioByExpanding
                    if scale_mode == 'keep_aspect_crop' else Qt.KeepAspectRatio)
            scaled = artwork.scaled(size, mode, Qt.SmoothTransformation)
            painter.drawImage((size.width() - scaled.width()) // 2,
                              (size.height() - scaled.height()) // 2, scaled)
    painter.end()
    return result


def _is_whiteout(image):
    sample = image.scaled(24, 14, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    rgba = sample.convertToFormat(QImage.Format_RGBA8888)
    pixels = bytes(rgba.constBits())
    white = sum(pixels[i] >= 235 and pixels[i + 1] >= 235 and pixels[i + 2] >= 235
                for i in range(0, len(pixels), 4))
    return white >= sample.width() * sample.height() * 0.80


class DesktopFrameSource:
    """DXGI capture owned and used exclusively by the cache worker thread."""
    def __init__(self):
        import dxcam
        from dxcam.core import Output
        from dxcam.util.io import enum_dxgi_adapters, enum_dxgi_outputs
        self.dxcam = dxcam
        self.cameras = {}
        self.outputs = []
        device_index = 0
        for adapter in enum_dxgi_adapters():
            outputs = enum_dxgi_outputs(adapter)
            if not outputs:
                continue
            for output_index, pointer in enumerate(outputs):
                output = Output(pointer)
                if not output.attached_to_desktop:
                    continue
                rect = output.desc.DesktopCoordinates
                self.outputs.append((device_index, output_index,
                                     (rect.left, rect.top, rect.right, rect.bottom)))
            device_index += 1

    def grab(self, geometry):
        x, y, width, height = geometry
        pieces = []
        for device, index, (left, top, right, bottom) in self.outputs:
            l, t, r, b = max(x, left), max(y, top), min(x + width, right), min(y + height, bottom)
            if r <= l or b <= t:
                continue
            key = device, index
            if key not in self.cameras:
                self.cameras[key] = self.dxcam.create(
                    device_idx=device, output_idx=index, output_color='BGRA',
                    processor_backend='numpy', max_buffer_len=2)
            frame = self.cameras[key].grab(
                region=(l - left, t - top, r - left, b - top),
                copy=True, new_frame_only=False)
            if frame is None:
                continue
            frame[:, :, 3] = 255
            image = QImage(frame.data, frame.shape[1], frame.shape[0],
                           frame.strides[0], QImage.Format_ARGB32).copy()
            pieces.append((l - x, t - y, image))
        if not pieces:
            return None
        if len(pieces) == 1 and pieces[0][0:2] == (0, 0) and pieces[0][2].size() == QSize(width, height):
            return pieces[0][2]
        result = QImage(width, height, QImage.Format_RGB32)
        result.fill(Qt.black)
        painter = QPainter(result)
        for px, py, image in pieces:
            painter.drawImage(px, py, image)
        painter.end()
        return result

    def close(self):
        for camera in self.cameras.values():
            camera.release()
        self.cameras.clear()


class PreFlashCache:
    def __init__(self, source_factory=DesktopFrameSource, fps=20, memory_limit=96 * 1024 * 1024):
        self._source_factory = source_factory
        self._interval = 1 / fps
        self._memory_limit = memory_limit
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._worker = None
        self._closed = False
        self._enabled = False
        self._context = None
        self._generation = 0
        self._suspended = False
        self._frozen = None
        self._frozen_age_ms = None
        self._frames = deque()
        self._bytes = 0
        self._error = ''
        self._capture_after = 0.0

    def set_enabled(self, enabled):
        with self._lock:
            enabled = bool(enabled)
            if enabled != self._enabled:
                self._enabled = enabled
                self._clear_locked()
        self._wake.set()

    def set_context(self, hwnd, geometry, foreground):
        context = (int(hwnd), tuple(geometry)) if hwnd and geometry and foreground else None
        with self._lock:
            if context != self._context:
                self._context = context
                self._clear_locked()
            if self._enabled and context and not self._closed and self._worker is None:
                self._worker = threading.Thread(target=self._run, daemon=True, name='PreFlashCapture')
                self._worker.start()
        self._wake.set()

    def _clear_locked(self):
        self._frames.clear()
        self._bytes = 0
        self._generation += 1

    def _store(self, image, timestamp, generation=None):
        if image is None or image.isNull() or _is_whiteout(image):
            return False
        with self._lock:
            if self._closed or self._suspended or (generation is not None and generation != self._generation):
                return False
            size = image.sizeInBytes()
            self._frames.append((timestamp, image))
            self._bytes += size
            while len(self._frames) > 8 or (self._bytes > self._memory_limit and len(self._frames) > 1):
                _, removed = self._frames.popleft()
                self._bytes -= removed.sizeInBytes()
            self._error = ''
        return True

    def freeze(self, now=None, lookback=0.15):
        now = time.perf_counter() if now is None else now
        with self._lock:
            if self._suspended:
                return self._frozen
            self._suspended = True
            candidates = [(stamp, frame) for stamp, frame in self._frames
                          if now - 2.0 <= stamp <= now - lookback]
            self._frozen = candidates[-1][1] if candidates else None
            self._frozen_age_ms = round((now - candidates[-1][0]) * 1000, 1) if candidates else None
            return self._frozen

    def resume(self):
        with self._lock:
            self._suspended = False
            self._frozen = None
            self._frozen_age_ms = None
            self._clear_locked()
            self._capture_after = time.perf_counter() + 0.1
        self._wake.set()

    def status(self):
        with self._lock:
            return {'frames': len(self._frames), 'bytes': self._bytes,
                    'frozen': self._frozen is not None, 'error': self._error,
                    'enabled': self._enabled, 'frozen_age_ms': self._frozen_age_ms,
                    'ready': bool(self._frames and time.perf_counter() - self._frames[0][0] >= 0.15)}

    def close(self):
        with self._lock:
            self._closed = True
            self._clear_locked()
            self._frozen = None
        self._wake.set()
        if self._worker is not None:
            self._worker.join(timeout=1)

    def _run(self):
        source = None
        com_initialized = False
        try:
            import comtypes
            comtypes.CoInitialize()
            com_initialized = True
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.GetForegroundWindow.argtypes = []
            user32.GetForegroundWindow.restype = wintypes.HWND
            while True:
                with self._lock:
                    if self._closed:
                        break
                    context = self._context
                    active = self._enabled and context is not None and not self._suspended
                    generation = self._generation
                    capture_after = self._capture_after
                if not active:
                    if not self._enabled and source is not None:
                        source.close()
                        source = None
                    self._wake.wait(0.1)
                    self._wake.clear()
                    continue
                hwnd, geometry = context
                if time.perf_counter() < capture_after:
                    self._wake.wait(min(self._interval, capture_after - time.perf_counter()))
                    self._wake.clear()
                    continue
                if user32.GetForegroundWindow() != hwnd:
                    self._wake.wait(self._interval)
                    self._wake.clear()
                    continue
                started = time.perf_counter()
                try:
                    if source is None:
                        source = self._source_factory()
                    image = source.grab(geometry)
                    if user32.GetForegroundWindow() == hwnd:
                        self._store(image, time.perf_counter(), generation)
                except Exception as error:
                    with self._lock:
                        self._error = type(error).__name__
                    if source is not None:
                        try:
                            source.close()
                        except Exception:
                            pass
                        source = None
                    self._wake.wait(1)
                    self._wake.clear()
                self._wake.wait(max(0, self._interval - (time.perf_counter() - started)))
                self._wake.clear()
        finally:
            try:
                if source is not None:
                    source.close()
            finally:
                if com_initialized:
                    comtypes.CoUninitialize()

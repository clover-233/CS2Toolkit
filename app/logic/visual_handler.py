import os
import ctypes
import webbrowser
import random
import json
import subprocess
import threading
import time
from PySide6.QtWidgets import QWidget, QLabel
from PySide6.QtCore import Qt, QUrl, QTimer, Signal, QObject, QPropertyAnimation, QSize
from PySide6.QtGui import QPixmap, QColor, QMovie, QImage
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from .audio_session_controller import AudioSessionController
from .game_window import GameWindowTracker
from .flash_background import (PreFlashCache, compose_flash_image,
                               image_has_transparency, flash_image_extensions)


class FlashSurface(QLabel):
    """A dedicated raster window, independent of native video surfaces/effects."""
    def __init__(self, flags):
        super().__init__(None, flags | Qt.WindowDoesNotAcceptFocus)
        self.setWindowTitle('CS2Toolkit Flash Surface')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAlignment(Qt.AlignCenter)
        self.setWindowOpacity(0.0)


class VisualSignals(QObject):
    update_flash = Signal(int)
    show_death = Signal()
    hide_death = Signal()
    minimize_game = Signal()
    restore_game = Signal()
    show_kill_icon = Signal(str)
    open_boss_key_target = Signal(str, str, int)  # target_type, target, delay(ms)
    close_browser = Signal()
    minimize_browser = Signal()
    pause_media = Signal()

class OverlayWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.game_window = GameWindowTracker()
        self._game_focused = False
        self._preview_active = False
        self.background_cache = PreFlashCache()
        self.flash_freeze_background_enabled = True
        self._capture_allowed = False
        self._flash_has_alpha = False
        self._flash_active = False
        self._frozen_background = None
        self._flash_render_key = None
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self._finish_flash_preview)

        self.flash_label = FlashSurface(self.windowFlags())
        self.flash_label.setAlignment(Qt.AlignCenter)
        # 不隐藏 flash_label，只通过透明度控制


        self.death_label = QLabel(self)
        self.death_label.setAlignment(Qt.AlignCenter)
        self.death_label.hide()
        self.death_label.setStyleSheet("background: transparent;")

        # 击杀图标控件（静态/GIF）
        self.kill_icon_label = QLabel(self)
        self.kill_icon_label.setScaledContents(True)
        self.kill_icon_label.hide()
        self.kill_icon_movie = None
        self.kill_icon_width = 120
        self.kill_icon_height = 120
        self.kill_icon_bottom = 100

        # 击杀图标视频控件（MP4/WEBM）
        self.kill_video_widget = QVideoWidget(self)
        self.kill_video_widget.hide()
        self.kill_media_player = QMediaPlayer(self)
        self.kill_audio_output = QAudioOutput(self)
        # 击杀视频通常不需要声音，避免干扰，设置静音
        self.kill_audio_output.setVolume(0)
        self.kill_media_player.setAudioOutput(self.kill_audio_output)
        self.kill_media_player.setVideoOutput(self.kill_video_widget)

        self.video_widget = QVideoWidget(self)
        self.video_widget.hide()

        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)

        self.flash_image_path = ""
        self.flash_scale_mode = "stretch"
        self._flash_folder_files = []
        self._flash_cached_source = ""
        self._flash_cached_pixmap = QPixmap()
        self._flash_display_pixmap = QPixmap()
        self.death_media_path = ""
        self.death_scale_mode = "stretch"

        # 淡出动画相关
        self.fade_timer = QTimer(self)
        self.fade_timer.timeout.connect(self._fade_out_step)
        self.current_opacity = 0.0

        # 击杀图标定时隐藏
        self.kill_icon_timer = QTimer(self)
        self.kill_icon_timer.timeout.connect(self.hide_kill_icon)

        from PySide6.QtWidgets import QGraphicsOpacityEffect
        self.flash_opacity_effect = QGraphicsOpacityEffect()
        self.kill_icon_opacity_effect = QGraphicsOpacityEffect()
        self.kill_video_opacity_effect = QGraphicsOpacityEffect()

        # Native window alpha avoids the first-use offscreen graphics-effect
        # render path shared with QVideoWidget on the main overlay.
        self.flash_opacity_effect.opacityChanged.connect(self._sync_flash_visibility)
        self.kill_icon_label.setGraphicsEffect(self.kill_icon_opacity_effect)
        self.kill_video_widget.setGraphicsEffect(self.kill_video_opacity_effect)

        self.flash_opacity_effect.setOpacity(0.0)
        self.kill_icon_opacity_effect.setOpacity(0.0)
        self.kill_video_opacity_effect.setOpacity(0.0)

        self.kill_icon_fade_in = QPropertyAnimation(self.kill_icon_opacity_effect, b"opacity")
        self.kill_icon_fade_in.setDuration(300)
        self.kill_icon_fade_in.setStartValue(0.0)
        self.kill_icon_fade_in.setEndValue(1.0)

        self.kill_video_fade_in = QPropertyAnimation(self.kill_video_opacity_effect, b"opacity")
        self.kill_video_fade_in.setDuration(300)
        self.kill_video_fade_in.setStartValue(0.0)
        self.kill_video_fade_in.setEndValue(1.0)

        self.kill_icon_fade_out = QPropertyAnimation(self.kill_icon_opacity_effect, b"opacity")
        self.kill_icon_fade_out.setDuration(500)
        self.kill_icon_fade_out.setStartValue(1.0)
        self.kill_icon_fade_out.setEndValue(0.0)
        self.kill_icon_fade_out.finished.connect(self._on_kill_icon_fade_out_finished)

        self.kill_video_fade_out = QPropertyAnimation(self.kill_video_opacity_effect, b"opacity")
        self.kill_video_fade_out.setDuration(500)
        self.kill_video_fade_out.setStartValue(1.0)
        self.kill_video_fade_out.setEndValue(0.0)
        self.kill_video_fade_out.finished.connect(self._on_kill_video_fade_out_finished)

        # 保持窗口和闪光标签始终处于显示状态，利用透明度控制可见性，彻底消除冷启动/窗口映射带来的系统级延迟
        self.flash_label.show()
        self.setWindowOpacity(0.0) # 初始设置为不可见，由焦点定时器控制
        self.show()

        # 定期更新窗口几何信息，避免在闪光触发瞬间调用 Windows API
        self.geometry_timer = QTimer(self)
        self.geometry_timer.timeout.connect(self._update_geometry_to_game)
        self.geometry_timer.start(500)
        self._update_geometry_to_game()

        # 高频监控游戏焦点，只有在 CS2 是前台窗口时才显示覆盖层
        self.focus_timer = QTimer(self)
        self.focus_timer.timeout.connect(self._check_game_focus)
        self.focus_timer.start(100)

    def _check_game_focus(self):
        focused = bool(self.game_window.foreground_game())
        if focused and not self._game_focused:
            self._update_geometry_to_game()
            self.game_window.place_overlay(self.winId())
        self._game_focused = focused
        hwnd = self.game_window.hwnd if focused else None
        geometry = self.game_window.client_geometry(hwnd) if hwnd else None
        self.background_cache.set_context(hwnd, geometry, focused)
        self._sync_flash_visibility()
        if not focused and not self._preview_active:
            if self.windowOpacity() > 0.0:
                self.setWindowOpacity(0.0)
        else:
            if self.windowOpacity() < 1.0:
                self.setWindowOpacity(1.0)

    def _update_geometry_to_game(self):
        if not self._preview_active:
            geometry = self.game_window.client_geometry(self.game_window.find())
            if geometry:
                self.game_window.place_overlay(self.winId(), geometry)
                self.game_window.place_overlay(self.flash_label.winId(), geometry)
        self._layout_media()

    def _sync_flash_visibility(self, *_):
        opacity = (self.flash_opacity_effect.opacity()
                   if self._game_focused or self._preview_active else 0.0)
        if abs(self.flash_label.windowOpacity() - opacity) > 0.001:
            self.flash_label.setWindowOpacity(opacity)

    def closeEvent(self, event):
        self.background_cache.close()
        self.flash_label.close()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'flash_label') and hasattr(self, 'flash_scale_mode'):
            self._layout_media()

    def _layout_media(self):
        self.death_label.setGeometry(self.rect())
        self.video_widget.setGeometry(self.rect())

        # 重新应用缩放模式，因为窗口大小变了
        if not self.flash_label.isHidden():
            self._render_flash_artwork()
        if not self.death_label.isHidden() and not self.death_media_path.lower().endswith(('.mp4', '.webm', '.avi')):
            self._update_label_pixmap(self.death_label, self.death_media_path, self.death_scale_mode)

        # 击杀图标放在下方正中
        icon_w = self.kill_icon_width
        icon_h = self.kill_icon_height
        icon_x = (self.width() - icon_w) // 2
        icon_y = self.height() - icon_h - self.kill_icon_bottom
        self.kill_icon_label.setGeometry(icon_x, icon_y, icon_w, icon_h)
        self.kill_video_widget.setGeometry(icon_x, icon_y, icon_w, icon_h)

    def set_flash_image(self, path, scale_mode="stretch"):
        changed = (
            path != self.flash_image_path
            or scale_mode != self.flash_scale_mode
        )
        self.flash_image_path = path
        self.flash_scale_mode = scale_mode
        if not changed:
            return

        self._flash_folder_files = []
        self._flash_cached_source = ""
        self._flash_cached_pixmap = QPixmap()
        if path and os.path.isdir(path):
            valid_exts = flash_image_extensions()
            self._flash_folder_files = [
                os.path.join(path, f)
                for f in os.listdir(path)
                if os.path.splitext(f)[1].lower() in valid_exts
            ]
        elif path and os.path.isfile(path):
            self._flash_cached_source = path
            self._flash_cached_pixmap = QPixmap(path)

        self._preload_flash_if_needed()

    def set_death_media(self, path, scale_mode="stretch"):
        self.death_media_path = path
        self.death_scale_mode = scale_mode

    def _update_label_pixmap(self, label, image_path, scale_mode):
        if not image_path or not os.path.exists(image_path):
            return

        if os.path.isdir(image_path):
            valid_exts = {'.png', '.jpg', '.jpeg', '.bmp'}
            files = [os.path.join(image_path, f) for f in os.listdir(image_path) if os.path.splitext(f)[1].lower() in valid_exts]
            if not files:
                return
            image_path = random.choice(files)

        pixmap = QPixmap(image_path)
        if scale_mode == 'stretch':
            label.setScaledContents(True)
            label.setPixmap(pixmap)
        else:
            label.setScaledContents(False)
            if scale_mode == 'keep_aspect_crop':
                aspect_mode = Qt.KeepAspectRatioByExpanding
            else:
                aspect_mode = Qt.KeepAspectRatio

            # If the label has a valid size, scale the pixmap
            if label.width() > 0 and label.height() > 0:
                scaled_pixmap = pixmap.scaled(label.size(), aspect_mode, Qt.SmoothTransformation)
                label.setPixmap(scaled_pixmap)
            else:
                label.setPixmap(pixmap)

    def _set_label_pixmap_from_pixmap(self, label, pixmap, scale_mode):
        if pixmap.isNull():
            label.clear()
            return

        if scale_mode == 'stretch':
            label.setScaledContents(True)
            label.setPixmap(pixmap)
            return

        label.setScaledContents(False)
        if scale_mode == 'keep_aspect_crop':
            aspect_mode = Qt.KeepAspectRatioByExpanding
        else:
            aspect_mode = Qt.KeepAspectRatio

        if label.width() > 0 and label.height() > 0:
            scaled_pixmap = pixmap.scaled(label.size(), aspect_mode, Qt.SmoothTransformation)
            label.setPixmap(scaled_pixmap)
        else:
            label.setPixmap(pixmap)

    def _prepare_flash_pixmap(self):
        # 预加载随机图片到内存
        if self._flash_folder_files:
            chosen = random.choice(self._flash_folder_files)
            pixmap = QPixmap(chosen)
            return chosen, pixmap
        return self._flash_cached_source, self._flash_cached_pixmap

    def _preload_flash_if_needed(self):
        # 提前准备好闪光图片，但不显示
        self.flash_label.setStyleSheet("background: transparent;")
        _, pixmap = self._prepare_flash_pixmap()
        self._flash_display_pixmap = pixmap
        self._flash_has_alpha = image_has_transparency(pixmap.toImage())
        self._flash_render_key = None
        self._refresh_background_capture()
        self._render_flash_artwork()

    def set_flash_background_enabled(self, enabled, capture_allowed=True):
        self.flash_freeze_background_enabled = bool(enabled)
        self._capture_allowed = bool(capture_allowed)
        self._refresh_background_capture()
        self._flash_render_key = None
        self._render_flash_artwork()

    def _refresh_background_capture(self):
        enabled = (self.flash_freeze_background_enabled and self._flash_has_alpha
                   and self._capture_allowed)
        self.background_cache.set_enabled(enabled)
        if not enabled:
            self._frozen_background = None
            self.background_cache.resume()
        elif self._flash_active:
            snapshot = self.background_cache.freeze()
            if not self._preview_active and self._frozen_background is None:
                self._frozen_background = snapshot

    def _render_flash_artwork(self):
        artwork = self._flash_display_pixmap
        background_key = (self._frozen_background.cacheKey()
                          if self._frozen_background is not None else None)
        compose = self._flash_active and self.flash_freeze_background_enabled and self._flash_has_alpha
        ratio = self.flash_label.devicePixelRatioF()
        size = QSize(max(1, round(self.flash_label.width() * ratio)),
                     max(1, round(self.flash_label.height() * ratio)))
        key = (artwork.cacheKey(), background_key, compose, size.width(), size.height(), self.flash_scale_mode)
        if key == self._flash_render_key:
            return
        self._flash_render_key = key
        if compose:
            frame = compose_flash_image(artwork.toImage(), self._frozen_background, size, self.flash_scale_mode)
            pixmap = QPixmap.fromImage(frame)
            pixmap.setDevicePixelRatio(ratio)
            self.flash_label.setScaledContents(True)
            self.flash_label.setPixmap(pixmap)
        else:
            self._set_label_pixmap_from_pixmap(self.flash_label, artwork, self.flash_scale_mode)

    def preview_flash(self):
        self._preload_flash_if_needed()
        if self._flash_display_pixmap.isNull():
            return False
        if self.flash_freeze_background_enabled and self._flash_has_alpha:
            if not self._preview_active:
                self._frozen_background = self.screen().grabWindow(0).toImage()
            self.background_cache.freeze()
        self._preview_active = True
        self.setGeometry(self.screen().geometry())
        self.flash_label.setGeometry(self.geometry())
        self._layout_media()
        self.setWindowOpacity(1.0)
        self.game_window.place_overlay(self.winId())
        self.update_flash(1)
        self.preview_timer.start(2500)
        return True

    def _finish_flash_preview(self):
        self._preview_active = False
        self.fade_timer.stop()
        self.current_opacity = 0.0
        self._flash_active = False
        self.flash_opacity_effect.setOpacity(0.0)
        self._frozen_background = None
        self.background_cache.resume()
        self._preload_flash_if_needed()
        self._update_geometry_to_game()
        self._check_game_focus()

    def update_flash(self, flash_value):
        if not self.flash_image_path or not os.path.exists(self.flash_image_path):
            self.fade_timer.stop()
            self.current_opacity = 0.0
            self._flash_active = False
            self._frozen_background = None
            self.background_cache.resume()
            self.flash_opacity_effect.setOpacity(0.0)
            self.flash_label.setPixmap(QPixmap())
            self._check_hide()
            return

        if flash_value > 0:
            if not self._flash_active:
                if self.flash_freeze_background_enabled and self._flash_has_alpha and not self._preview_active:
                    self._frozen_background = self.background_cache.freeze()
                self._flash_active = True
            self._render_flash_artwork()
            # 几何信息已由 geometry_timer 定期更新，无需在此处调用

            # 只要 flashed 为 1，透明度直接设为 1 (100% 显示)
            self.fade_timer.stop()
            self.current_opacity = 1.0
            self.flash_opacity_effect.setOpacity(1.0)
            self.flash_label.raise_()
            self._check_game_focus()
            if self._game_focused or self._preview_active:
                self.game_window.place_overlay(self.flash_label.winId())
                # Flush the first raster frame before any audio worker starts.
                self.flash_label.repaint()
        else:
            # 当 flashed 变为 0 时，如果图片正在显示，则启动淡出动画
            if self.current_opacity > 0 and not self.fade_timer.isActive():
                self.fade_timer.start(50)  # 每 50ms 更新一次透明度

    def _fade_out_step(self):
        self.current_opacity -= 0.05  # 每次减少 0.05，约 1 秒淡出
        if self.current_opacity <= 0:
            self.current_opacity = 0
            self._flash_active = False
            self._frozen_background = None
            self.background_cache.resume()
            self.fade_timer.stop()
            self.flash_opacity_effect.setOpacity(0.0)
            self.flash_label.setPixmap(QPixmap()) # 释放闪光图内存
            self.flash_label.setStyleSheet("background: transparent;")
            self._preload_flash_if_needed() # 淡出结束后立即为下一次闪光准备好资源
            self._check_hide()
            return
        self.flash_opacity_effect.setOpacity(self.current_opacity)

    def show_death(self):
        if not self.death_media_path or not os.path.exists(self.death_media_path):
            return

        # 窗口透明度和显示已由焦点定时器 _check_game_focus 自动管理

        if self.death_media_path.lower().endswith(('.mp4', '.webm', '.avi')):
            # QVideoWidget 默认自带等比例拉伸，如果是拉伸拉满需要设置 aspect ratio mode
            if self.death_scale_mode == 'stretch':
                self.video_widget.setAspectRatioMode(Qt.IgnoreAspectRatio)
            elif self.death_scale_mode == 'keep_aspect_crop':
                self.video_widget.setAspectRatioMode(Qt.KeepAspectRatioByExpanding)
            else:
                self.video_widget.setAspectRatioMode(Qt.KeepAspectRatio)

            self.video_widget.show()
            self.media_player.setSource(QUrl.fromLocalFile(self.death_media_path))
            self.media_player.play()
        else:
            self._update_label_pixmap(self.death_label, self.death_media_path, self.death_scale_mode)
            self.death_label.show()

    def hide_death(self):
        self.death_label.hide()
        self.death_label.setPixmap(QPixmap()) # 释放静态图内存
        self.video_widget.hide()
        self.media_player.stop()
        self.media_player.setSource(QUrl())  # 释放内存
        self._check_hide()

    def show_kill_icon(self, path):
        if not path or not os.path.exists(path):
            return
        self._update_geometry_to_game()

        # 停止可能正在运行的淡出动画和定时器
        self.kill_icon_fade_out.stop()
        self.kill_video_fade_out.stop()
        self.kill_icon_timer.stop()

        # 窗口透明度和显示已由焦点定时器 _check_game_focus 自动管理

        is_video = path.lower().endswith(('.mp4', '.webm', '.avi'))
        is_gif = path.lower().endswith('.gif')

        if is_video:
            self.kill_icon_label.hide()
            if self.kill_icon_movie:
                self.kill_icon_movie.stop()

            self.kill_media_player.setSource(QUrl.fromLocalFile(path))
            self.kill_video_widget.show()
            self.kill_media_player.play()
            self.kill_video_fade_in.start()
        elif is_gif:
            self.kill_video_widget.hide()
            self.kill_media_player.stop()

            if self.kill_icon_movie:
                self.kill_icon_movie.stop()

            self.kill_icon_movie = QMovie(path)
            self.kill_icon_label.setMovie(self.kill_icon_movie)
            self.kill_icon_movie.start()
            self.kill_icon_label.show()
            self.kill_icon_fade_in.start()
        else:
            self.kill_video_widget.hide()
            self.kill_media_player.stop()

            if self.kill_icon_movie:
                self.kill_icon_movie.stop()

            self.kill_icon_label.setPixmap(QPixmap(path))
            self.kill_icon_label.show()
            self.kill_icon_fade_in.start()

        # 显示3秒后触发淡出
        self.kill_icon_timer.start(3000)

    def hide_kill_icon(self):
        self.kill_icon_timer.stop()
        if not self.kill_icon_label.isHidden():
            self.kill_icon_fade_out.start()
        if not self.kill_video_widget.isHidden():
            self.kill_video_fade_out.start()

    def _on_kill_icon_fade_out_finished(self):
        self.kill_icon_label.hide()
        if self.kill_icon_movie:
            self.kill_icon_movie.stop()
            self.kill_icon_label.setMovie(None) # 释放GIF内存
        self.kill_icon_label.setPixmap(QPixmap()) # 释放静态图内存
        self._check_hide()

    def _on_kill_video_fade_out_finished(self):
        self.kill_video_widget.hide()
        self.kill_media_player.stop()
        self.kill_media_player.setSource(QUrl()) # 释放视频内存
        self._check_hide()

    def _check_hide(self):
        pass # 我们现在保持窗口常驻以消除延迟，所以不再隐藏窗口



class VisualHandler(QObject):
    def __init__(self, config_manager):
        super().__init__()
        self.config_manager = config_manager
        self.signals = VisualSignals()

        self.overlay = OverlayWindow()
        self.signals.update_flash.connect(self.overlay.update_flash)
        self.overlay.preview_timer.timeout.connect(self._restore_flash_after_preview)
        self.signals.show_death.connect(self.overlay.show_death)
        self.signals.hide_death.connect(self.overlay.hide_death)
        self.signals.show_kill_icon.connect(self.overlay.show_kill_icon)

        self.signals.minimize_game.connect(self._minimize_cs2)
        self.signals.restore_game.connect(self._restore_cs2)
        self.signals.open_boss_key_target.connect(self._open_boss_key_target)
        self.signals.close_browser.connect(self._close_browser)
        self.signals.minimize_browser.connect(self._minimize_browser)
        self.signals.pause_media.connect(self._pause_media)
        self.audio_session_controller = AudioSessionController()

        self.is_flashed = False
        self.is_dead = False
        self._boss_key_triggered = False
        self._boss_key_restore_pending = False
        self._browser_opened = False
        self._boss_key_target_opened = False
        self._browser_muted = False
        self._last_match_kills = -1
        self._round_start_kills = 0
        self._visual_config_signature = None
        self._flash_volume_reduced = False
        self.last_gsi_time = None
        self.last_flash_value = None
        self.last_is_observing = False
        self.last_flash_trigger_time = None

        self.config_timer = QTimer(self)
        self.config_timer.timeout.connect(self._update_config)
        self.config_timer.start(300)

        self._update_config(force=True)

    def _restore_flash_after_preview(self):
        if self.is_flashed:
            self.overlay.update_flash(1)

    def _update_config(self, force=False):
        config = self.config_manager.get('visual', {})
        signature = json.dumps(config, ensure_ascii=False, sort_keys=True)
        if not force and signature == self._visual_config_signature:
            return
        self._visual_config_signature = signature

        self.visual_master_enabled = config.get('visual_master_enabled', True)
        self.flash_enabled = config.get('flash_enabled', False)
        self.flash_path = config.get('flash_path', '')
        self.flash_scale_mode = config.get('flash_scale_mode', 'stretch')
        self.flash_reduce_volume_enabled = config.get('flash_reduce_volume_enabled', False)
        self.flash_reduce_volume_percent = config.get('flash_reduce_volume_percent', 25)
        if not (self.visual_master_enabled and self.flash_enabled and self.flash_reduce_volume_enabled):
            self._restore_flash_audio_reduction()
        self.overlay.set_flash_image(
            self.flash_path,
            self.flash_scale_mode
        )
        self.overlay.set_flash_background_enabled(
            config.get('flash_freeze_background_enabled', True),
            self.visual_master_enabled and self.flash_enabled)

        self.death_media_enabled = config.get('death_media_enabled', False)
        self.death_media_path = config.get('death_media_path', '')
        self.overlay.set_death_media(self.death_media_path, config.get('death_scale_mode', 'stretch'))

        self.boss_key_enabled = config.get('boss_key_enabled', False)
        self.boss_key_target_type = config.get('boss_key_target_type', 'url')
        self.boss_key_target = config.get('boss_key_target', config.get('boss_key_url', 'https://www.baidu.com'))
        self.boss_key_delay = config.get('boss_key_delay', 0)
        self.boss_key_action = config.get('boss_key_action', 'none')

        self.kill_icon_enabled = config.get('kill_icon_enabled', False)
        self.kill_icon_is_advanced = config.get('kill_icon_is_advanced', False)
        self.kill_icon_path = config.get('kill_icon_path', '')
        self.kill_icons_1_5 = config.get('kill_icons_1_5', [{} for _ in range(5)])
        self.overlay.kill_icon_width = config.get('kill_icon_width', 120)
        self.overlay.kill_icon_height = config.get('kill_icon_height', 120)
        self.overlay.kill_icon_bottom = config.get('kill_icon_bottom', 100)
        if (not self.visual_master_enabled or not self.flash_enabled) and self.is_flashed:
            self.is_flashed = False
            self.signals.update_flash.emit(0)
            self._restore_flash_audio_reduction()

    def process_gsi(self, game_state: dict):
        self._update_config()
        self.last_gsi_time = time.monotonic()
        self.last_flash_value = game_state.get('player', {}).get('state', {}).get('flashed')

        if not self.visual_master_enabled:
            if self.is_flashed:
                self.is_flashed = False
                self.signals.update_flash.emit(0)
                self._restore_flash_audio_reduction()
            if self.is_dead and self.death_media_enabled:
                self.signals.hide_death.emit()
            if self._should_restore_boss_key("", 100, False):
                self._restore_boss_key_context()
            return

        player = game_state.get('player', {})
        if not player:
            if self.is_flashed:
                self.is_flashed = False
                self.signals.update_flash.emit(0)
                self._restore_flash_audio_reduction()
            return

        state = player.get('state', {})
        activity = player.get('activity', '')
        spectarget = player.get('spectarget')

        # 如果是观战状态，不处理闪光和切屏
        # 观战检测逻辑需要更严谨，防止被他人闪光或死亡影响
        has_valid_team = player.get('team', '') in ['T', 'CT']
        provider = game_state.get('provider', {})
        provider_steamid = provider.get('steamid')
        player_steamid = player.get('steamid')
        health = state.get('health', 100)

        is_observing = False
        if provider_steamid and player_steamid and provider_steamid != player_steamid:
            is_observing = True
        elif (spectarget not in (None, 0, '0', -1, '-1', '')
              or activity == 'spectating' or not has_valid_team):
            is_observing = True
        self.last_is_observing = is_observing

        # 1. 闪光弹处理
        if self.flash_enabled and not is_observing:
            flash_active = state.get('flashed', 0) > 0
            if flash_active != self.is_flashed:
                self.is_flashed = flash_active
                if flash_active:
                    self.last_flash_trigger_time = time.monotonic()
                self.signals.update_flash.emit(1 if flash_active else 0)
                if flash_active:
                    self._apply_flash_audio_reduction()
                else:
                    self._restore_flash_audio_reduction()
        elif self.is_flashed:
            self.is_flashed = False
            self.signals.update_flash.emit(0)
            self._restore_flash_audio_reduction()

        # 2. 死亡与复活处理
        round_info = game_state.get('round', {})
        phase = round_info.get('phase', '')

        # 处理死亡显示和 Boss Key 触发
        if not is_observing and health == 0 and not self.is_dead:
            self.is_dead = True
            if self.death_media_enabled:
                self.signals.show_death.emit()

            if self.boss_key_enabled:
                self._boss_key_triggered = True
                self._boss_key_restore_pending = True
                self.signals.open_boss_key_target.emit(self.boss_key_target_type, self.boss_key_target, self.boss_key_delay * 1000)

        elif health > 0 and self.is_dead:
            self.is_dead = False
            if self.death_media_enabled:
                self.signals.hide_death.emit()

        # 观战状态下强制清空死亡UI，但千万不能重置 is_dead = False，否则会打断 Boss Key 的状态判定
        if is_observing and self.is_dead:
            if self.death_media_enabled:
                self.signals.hide_death.emit()

        # 3. 回合重置恢复游戏和连杀重置
        # 触发条件：玩家处于非观战状态，且已存活，且之前触发了 Boss Key 或被静音过
        if self._should_restore_boss_key(phase, health, is_observing):
            self._restore_boss_key_context()

        if phase == 'freezetime' and getattr(self, '_last_phase', '') != 'freezetime':
            # Round reset
            match_stats = player.get('match_stats', {})
            self._round_start_kills = match_stats.get('kills', 0)

        # 4. 击杀图标处理
        if self.kill_icon_enabled and not is_observing:
            match_stats = player.get('match_stats', {})
            current_match_kills = match_stats.get('kills', 0)

            if self._last_match_kills == -1:
                self._last_match_kills = current_match_kills
                self._round_start_kills = current_match_kills

            if current_match_kills > self._last_match_kills:
                self._last_match_kills = current_match_kills
                current_round_kills = current_match_kills - self._round_start_kills

                # 决定显示的图标
                icon_to_show = ""
                if self.kill_icon_is_advanced:
                    idx = min(current_round_kills - 1, 4)
                    icon_to_show = self.kill_icons_1_5[idx].get('path', '')
                    if not icon_to_show or not os.path.exists(icon_to_show):
                        icon_to_show = self.kill_icon_path
                else:
                    icon_to_show = self.kill_icon_path

                if icon_to_show and os.path.exists(icon_to_show):
                    self.signals.show_kill_icon.emit(icon_to_show)
            elif current_match_kills < self._last_match_kills:
                self._last_match_kills = current_match_kills
        elif is_observing:
            match_stats = player.get('match_stats', {})
            self._last_match_kills = match_stats.get('kills', 0)

        self._last_phase = phase

    def flash_status(self):
        self._update_config()
        if not self.visual_master_enabled:
            return "视觉总开关已关闭"
        if not self.flash_enabled:
            return "闪光替换已关闭"
        if self.overlay._flash_display_pixmap.isNull():
            return "图片无法读取，请重新选择 PNG/JPG/BMP 图片"
        suffix = ''
        if self.overlay.flash_freeze_background_enabled and self.overlay._flash_has_alpha:
            cache = self.overlay.background_cache.status()
            if self.overlay._flash_active:
                suffix = '；背景：闪光前静止画面' if self.overlay._frozen_background is not None else '；背景尚未就绪，本次使用深色底'
            elif cache['error']:
                suffix = '；原画面捕获暂不可用'
            else:
                suffix = '；原画面缓存就绪' if cache['ready'] else '；原画面缓存中，请切回游戏稍候'
        if self.last_gsi_time is None:
            return "图片就绪，等待游戏数据；请进入地图，配置变更后需重启 CS2" + suffix
        age = int(time.monotonic() - self.last_gsi_time)
        value = self.last_flash_value
        event = f"闪光值 {value}" if value is not None else "本次数据没有闪光字段"
        observing = "，当前判定为观战" if self.last_is_observing else ""
        focus = "，CS2 在前台" if self.overlay._game_focused else "，CS2 不在前台"
        triggered = (f"；{int(time.monotonic() - self.last_flash_trigger_time)} 秒前触发过替换"
                     if self.last_flash_trigger_time is not None else "；尚未触发替换")
        return f"{age} 秒前收到游戏数据，{event}{observing}{focus}{triggered}{suffix}"

    def _should_restore_boss_key(self, phase, health, is_observing):
        if not (self._boss_key_triggered or self._boss_key_restore_pending or self._browser_muted):
            return False

        if is_observing:
            return False

        if phase == 'freezetime' and getattr(self, '_last_phase', '') != 'freezetime':
            return True

        if health > 0 and phase == 'live':
            return True

        return False

    def _restore_boss_key_context(self):
        self._boss_key_triggered = False
        self._boss_key_restore_pending = False

        if self.boss_key_action == 'close':
            self.signals.close_browser.emit()
            self._browser_opened = False
        elif self.boss_key_action == 'pause':
            is_playing = self._is_audio_playing()
            if self._browser_muted and is_playing:
                self.signals.pause_media.emit()
            self._minimize_browser()

        # 切回游戏时也稍微延迟一下，确保之前的动作（比如关闭浏览器）已经执行
        QTimer.singleShot(100, lambda: self.signals.restore_game.emit())

        self._browser_muted = False
        self._boss_key_target_opened = False

    def _apply_flash_audio_reduction(self):
        if not self.flash_reduce_volume_enabled or self._flash_volume_reduced:
            return
        self._flash_volume_reduced = True
        self.audio_session_controller.request_reduction(["cs2.exe"], self.flash_reduce_volume_percent)

    def _restore_flash_audio_reduction(self):
        if not self._flash_volume_reduced:
            return
        self._flash_volume_reduced = False
        self.audio_session_controller.request_reduction()

    def _minimize_cs2(self):
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "Counter-Strike 2")
        if hwnd:
            # 最小化之前，先解除鼠标锁定（如果是独占模式可能需要）
            user32.ClipCursor(None)
            # 先尝试通过发送 WM_SYSCOMMAND SC_MINIMIZE 消息来最小化，这通常更平滑
            user32.PostMessageW(hwnd, 0x0112, 0xF020, 0)

            # 为了确保全屏模式下也能可靠最小化并释放焦点，补充一个 ShowWindow 最小化非激活
            # SW_SHOWMINNOACTIVE = 7
            user32.ShowWindow(hwnd, 7)

    def _restore_cs2(self):
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "Counter-Strike 2")
        if hwnd:
            # 强制解除当前可能的其他窗口置顶状态
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32

            # 1. 尝试直接把窗口提到前台
            user32.SetForegroundWindow(hwnd)

            # 2. 如果窗口被最小化了，用 ShowWindow 恢复它 (SW_RESTORE = 9)
            user32.ShowWindow(hwnd, 9)

            # 3. 再次尝试置前，如果前一次失败
            user32.SetForegroundWindow(hwnd)

            # 4. 有时候 Windows 会阻止程序抢占前台，使用按键模拟欺骗系统
            # 模拟按下和释放一个无关紧要的键 (比如 ALT 键的左边部分)
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 0x0002, 0)

            # 5. 最后一次尝试置前
            user32.SetForegroundWindow(hwnd)

    def _open_boss_key_target(self, target_type, target, delay_ms):
        def _action(t_type=target_type, t_val=target):
            # 如果在延迟期间玩家已经复活，则取消切屏
            if not getattr(self, '_boss_key_triggered', False):
                return

            # 首先最小化 CS2
            self.signals.minimize_game.emit()

            if t_type == "app":
                if t_val and os.path.exists(t_val):
                    try:
                        os.startfile(t_val)
                        self._boss_key_target_opened = True
                    except OSError as e:
                        print(f"启动本地程序失败: {e}")
            else:
                if not t_val.startswith(('http://', 'https://')):
                    t_val = 'https://' + t_val

                # 严格控制：只要在这局游戏运行期间打开过一次浏览器，就绝对不再调用 openUrl 弹新标签
                if not getattr(self, '_browser_opened', False):
                    from PySide6.QtGui import QDesktopServices
                    QDesktopServices.openUrl(QUrl(t_val))
                    self._browser_opened = True
                else:
                    # 尝试将已有的浏览器窗口置于前台
                    success = self._activate_browser_window()
                    if not success:
                        # 如果系统里确实没找到任何浏览器窗口（可能用户手动关掉了），则重新打开
                        from PySide6.QtGui import QDesktopServices
                        QDesktopServices.openUrl(QUrl(t_val))
                        self._browser_opened = True

            if self.boss_key_action == 'pause':
                # 在切出游戏时发送暂停/播放媒体键
                # 只有当系统没有发声（处于暂停状态）时，我们才发送“播放”指令
                is_playing = self._is_audio_playing()
                if not is_playing:
                    self._pause_media()
                # 必须标记为 True，这样在新回合开始时程序才知道需要再发一次按键恢复播放
                self._browser_muted = True

        if delay_ms > 0:
            QTimer.singleShot(delay_ms, _action)
        else:
            # 使用 timer 稍微延迟一下动作，确保先让出焦点
            QTimer.singleShot(100, _action)

    def _activate_browser_window(self):
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            def get_process_name(pid):
                # 使用 PROCESS_QUERY_LIMITED_INFORMATION (0x1000) 替代 PROCESS_VM_READ，避免权限不足导致获取不到进程名
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                hProcess = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if hProcess:
                    exe_name = ctypes.create_unicode_buffer(260)
                    size = wintypes.DWORD(260)
                    if kernel32.QueryFullProcessImageNameW(hProcess, 0, exe_name, ctypes.byref(size)):
                        kernel32.CloseHandle(hProcess)
                        return exe_name.value
                    kernel32.CloseHandle(hProcess)
                return ""

            found = False

            def enum_windows_proc(hwnd, lParam):
                nonlocal found
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        class_name = ctypes.create_unicode_buffer(256)
                        user32.GetClassNameW(hwnd, class_name, 256)
                        cname = class_name.value

                        is_browser = False
                        # 通过类名快速匹配大多数主流浏览器
                        if cname in ['Chrome_WidgetWin_1', 'MozillaWindowClass']:
                            # 但是要排除掉 QQ 和 微信 等也使用了 Chromium 内核的软件
                            pid = wintypes.DWORD()
                            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            exe_path = get_process_name(pid.value).lower()
                            exe_name = os.path.basename(exe_path)

                            # 只有当它确实是浏览器进程时才判定为浏览器
                            browsers = ['msedge.exe', 'chrome.exe', 'firefox.exe', '360se.exe', 'iexplore.exe', 'sogouexplorer.exe', 'browser.exe', 'yandex.exe', 'opera.exe', 'brave.exe']
                            if any(b in exe_name for b in browsers):
                                is_browser = True
                        else:
                            # 兜底：通过进程名匹配
                            pid = wintypes.DWORD()
                            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            exe_path = get_process_name(pid.value).lower()
                            exe_name = os.path.basename(exe_path)

                            browsers = ['msedge.exe', 'chrome.exe', 'firefox.exe', '360se.exe', 'iexplore.exe', 'sogouexplorer.exe', 'browser.exe', 'yandex.exe', 'opera.exe', 'brave.exe']

                            if any(b in exe_name for b in browsers):
                                is_browser = True

                        if is_browser:
                            # 过滤掉一些幽灵窗口 (ToolWindow)
                            GWL_EXSTYLE = -20
                            WS_EX_TOOLWINDOW = 0x00000080
                            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                            if not (style & WS_EX_TOOLWINDOW):
                                # 绕过前台窗口限制
                                current_thread = kernel32.GetCurrentThreadId()
                                fg_hwnd = user32.GetForegroundWindow()

                                # 使用更安全的方式定义 WINDOWPLACEMENT
                                class WINDOWPLACEMENT(ctypes.Structure):
                                    _fields_ = [
                                        ("length", wintypes.UINT),
                                        ("flags", wintypes.UINT),
                                        ("showCmd", wintypes.UINT),
                                        ("ptMinPosition", wintypes.POINT),
                                        ("ptMaxPosition", wintypes.POINT),
                                        ("rcNormalPosition", wintypes.RECT)
                                    ]

                                # 我们这里获取窗口原本的显示状态
                                placement = WINDOWPLACEMENT()
                                placement.length = ctypes.sizeof(WINDOWPLACEMENT)
                                user32.GetWindowPlacement(hwnd, ctypes.byref(placement))

                                # SW_SHOWMAXIMIZED = 3, SW_RESTORE = 9, WPF_RESTORETOMAXIMIZED = 2
                                # 如果当前是最大化(3)，或者最小化之前是最大化(flags & 2)，则恢复为最大化
                                WPF_RESTORETOMAXIMIZED = 0x0002
                                if placement.showCmd == 3 or (placement.flags & WPF_RESTORETOMAXIMIZED):
                                    show_cmd = 3
                                else:
                                    show_cmd = 9

                                if fg_hwnd:
                                    fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
                                    if fg_thread != current_thread:
                                        user32.AttachThreadInput(current_thread, fg_thread, True)
                                        user32.ShowWindow(hwnd, show_cmd)
                                        user32.SetForegroundWindow(hwnd)
                                        user32.AttachThreadInput(current_thread, fg_thread, False)
                                        found = True
                                        return False # 停止枚举

                                user32.ShowWindow(hwnd, show_cmd)
                                user32.SetForegroundWindow(hwnd)
                                found = True
                                return False # 停止枚举
                return True

            EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(EnumWindowsProc(enum_windows_proc), 0)
            return found
        except Exception as e:
            print(f"激活浏览器窗口失败: {e}")
            return False

    def _minimize_browser(self):
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            def get_process_name(pid):
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                hProcess = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if hProcess:
                    exe_name = ctypes.create_unicode_buffer(260)
                    size = wintypes.DWORD(260)
                    if kernel32.QueryFullProcessImageNameW(hProcess, 0, exe_name, ctypes.byref(size)):
                        kernel32.CloseHandle(hProcess)
                        return exe_name.value
                    kernel32.CloseHandle(hProcess)
                return ""

            def enum_windows_proc(hwnd, lParam):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        class_name = ctypes.create_unicode_buffer(256)
                        user32.GetClassNameW(hwnd, class_name, 256)
                        cname = class_name.value

                        target_app_exe = ""
                        if getattr(self, 'boss_key_target_type', 'url') == 'app' and getattr(self, 'boss_key_target', ''):
                            app_exe_name = os.path.basename(self.boss_key_target).lower()
                            if app_exe_name.endswith('.lnk'):
                                try:
                                    import win32com.client
                                    shell = win32com.client.Dispatch("WScript.Shell")
                                    shortcut = shell.CreateShortCut(self.boss_key_target)
                                    app_exe_name = os.path.basename(shortcut.Targetpath).lower()
                                except Exception:
                                    pass
                            target_app_exe = app_exe_name

                        is_browser = False
                        if cname in ['Chrome_WidgetWin_1', 'MozillaWindowClass']:
                            pid = wintypes.DWORD()
                            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            exe_path = get_process_name(pid.value).lower()
                            exe_name = os.path.basename(exe_path)
                            browsers = ['msedge.exe', 'chrome.exe', 'firefox.exe', '360se.exe', 'iexplore.exe', 'sogouexplorer.exe', 'browser.exe', 'yandex.exe', 'opera.exe', 'brave.exe']
                            if any(b in exe_name for b in browsers):
                                is_browser = True
                        else:
                            pid = wintypes.DWORD()
                            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            exe_path = get_process_name(pid.value).lower()
                            exe_name = os.path.basename(exe_path)
                            browsers = ['msedge.exe', 'chrome.exe', 'firefox.exe', '360se.exe', 'iexplore.exe', 'sogouexplorer.exe', 'browser.exe', 'yandex.exe', 'opera.exe', 'brave.exe']
                            if any(b in exe_name for b in browsers):
                                is_browser = True

                        # 如果是目标程序，也一并最小化
                        if target_app_exe:
                            pid = wintypes.DWORD()
                            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            exe_path = get_process_name(pid.value).lower()
                            exe_name = os.path.basename(exe_path)
                            if target_app_exe in exe_name:
                                is_browser = True

                        if is_browser:
                            user32.PostMessageW(hwnd, 0x0112, 0xF020, 0) # WM_SYSCOMMAND, SC_MINIMIZE
                return True

            EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(EnumWindowsProc(enum_windows_proc), 0)
        except Exception as e:
            print(f"最小化浏览器失败: {e}")

    def _pause_media(self):
        try:
            # 模拟按下键盘的 播放/暂停 媒体键 (VK_MEDIA_PLAY_PAUSE = 0xB3)
            user32 = ctypes.windll.user32
            user32.keybd_event(0xB3, 0, 0, 0) # Key Down
            user32.keybd_event(0xB3, 0, 2, 0) # Key Up
        except Exception as e:
            print(f"发送媒体暂停键失败: {e}")

    def _is_audio_playing(self):
        """检测目标浏览器或程序是否真的在输出音频，避免“手动暂停后仍被误判为播放中”"""
        try:
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
            sessions = AudioUtilities.GetAllSessions()

            # 只关心主流浏览器的真实输出音量，忽略 QQ/系统提示音等其他软件
            target_browsers = [
                'msedge.exe', 'chrome.exe', 'firefox.exe', '360se.exe',
                'iexplore.exe', 'sogouexplorer.exe', 'browser.exe',
                'yandex.exe', 'opera.exe', 'brave.exe'
            ]

            # 如果配置了本地程序，也把它加进检测名单
            if getattr(self, 'boss_key_target_type', 'url') == 'app' and getattr(self, 'boss_key_target', ''):
                exe_name = os.path.basename(self.boss_key_target).lower()
                if exe_name.endswith('.lnk'):
                    try:
                        import win32com.client
                        shell = win32com.client.Dispatch("WScript.Shell")
                        shortcut = shell.CreateShortCut(self.boss_key_target)
                        exe_name = os.path.basename(shortcut.Targetpath).lower()
                    except Exception:
                        pass
                if exe_name and exe_name not in target_browsers:
                    target_browsers.append(exe_name)

            for session in sessions:
                if not session.Process:
                    continue

                process_name = session.Process.name().lower()
                if not any(browser == process_name for browser in target_browsers):
                    continue

                try:
                    meter = session._ctl.QueryInterface(IAudioMeterInformation)
                    peak_value = meter.GetPeakValue()
                except Exception:
                    peak_value = 0.0

                # 只有浏览器会话存在真实音频输出时，才认为媒体正在播放
                if peak_value > 0.001:
                    return True

            return False
        except Exception as e:
            print(f"音频状态检测失败: {e}")
            return False

    def _close_browser(self):
        # 如果是本地程序，尝试关闭对应的进程
        if getattr(self, 'boss_key_target_type', 'url') == 'app':
            target = getattr(self, 'boss_key_target', '')
            if target:
                exe_name = os.path.basename(target)
                if exe_name.lower().endswith('.lnk'):
                    try:
                        import win32com.client
                        shell = win32com.client.Dispatch("WScript.Shell")
                        shortcut = shell.CreateShortCut(target)
                        exe_name = os.path.basename(shortcut.Targetpath)
                    except Exception:
                        pass
                if exe_name.lower().endswith(('.exe', '.bat', '.cmd')):
                    subprocess.run(["taskkill", "/F", "/IM", exe_name], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            # 强制关闭常见的浏览器进程
            browsers = ["msedge.exe", "chrome.exe", "firefox.exe", "360se.exe", "iexplore.exe", "sogouexplorer.exe"]
            for b in browsers:
                subprocess.run(["taskkill", "/F", "/IM", b], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

    def cleanup(self):
        self.config_timer.stop()
        self.overlay.preview_timer.stop()
        self.overlay.geometry_timer.stop()
        self.overlay.focus_timer.stop()
        self.overlay.fade_timer.stop()
        self.overlay.close()
        self._restore_flash_audio_reduction()
        self.audio_session_controller.close()

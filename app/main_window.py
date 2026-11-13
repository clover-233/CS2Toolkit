import os
import json
import threading
from PySide6.QtWidgets import QApplication, QFileDialog, QListWidgetItem
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QImage, QIcon, QDesktopServices
from PySide6.QtCore import QUrl
from qfluentwidgets import (setTheme, Theme, InfoBar, FluentWindow, NavigationItemPosition,
                           MessageBox, FluentIcon as FIF, setThemeColor, CheckBox, MessageBoxBase, SubtitleLabel, BodyLabel)
from PySide6.QtGui import QColor, QAction, QPixmap, QPainter, QImage
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QLabel, QGraphicsBlurEffect
from app.logic.config_manager import ConfigManager
from app.logic.font_replacer import FontReplacer
from app.logic.video_replacer import VideoReplacer
from app.logic.steam_utils import SteamUtils, PathValidator
from app.logic.sound_replacer import SoundReplacer
from app.logic.gsi_manager import GSIManager
from app.logic.visual_handler import VisualHandler
from app.logic.go_pet_manager import GoPetManager
from app.logic.launch_manager import LaunchManager
from app.release_endpoints import UPDATE_URL
from app.ui.animations import AnimationManager
from app.ui.fluent_dialogs import TextInputDialog
from app.ui.pages.home_page import HomePage
from app.ui.integrated_sound_page import IntegratedSoundPage
from app.ui.components import VideoPresetWidget, FontPresetWidget, SoundPresetWidget
from app.assets import app_icon

# Qt 资源文件：提供所有 :/xxx.ico 图标路径
import app.assets.video_resources  # noqa: F401

class GsiSignalEmitter(QObject):
    data_received = Signal(dict)

class UpdateSignalEmitter(QObject):
    update_found = Signal(dict)

class CS2Tool(FluentWindow):
    def __init__(self):
        super().__init__()
        self.version = "1.5.1-alpha"
        self.repo_url = "https://github.com/clover-233/CS2Toolkit"
        self.is_dark_mode = False
        self._force_quit = False
        self.config_manager = ConfigManager()

        # 1. 设定主题与品牌色
        setThemeColor(QColor("#0078D4")) # 品牌蓝色
        theme_val = self.config_manager.get("theme", "Auto")
        if theme_val == "Light":
            setTheme(Theme.LIGHT)
            self.is_dark_mode = False
        elif theme_val == "Dark":
            setTheme(Theme.DARK)
            self.is_dark_mode = True
        else:
            setTheme(Theme.AUTO)

        self.animation_manager = AnimationManager(self)
        self.gsi_manager = GSIManager(config_manager=self.config_manager)
        self.visual_handler = VisualHandler(self.config_manager)
        self.go_pet_manager = GoPetManager(self.config_manager)
        self.steam_path = ""
        self.video_path = ""
        self.font_path = ""
        self.sound_path = ""
        self.last_game_state_overview = {
            "phase": "等待 GSI",
            "activity": "未连接",
            "team": "--",
            "health": "--",
        }

        self.gsi_signal_emitter = GsiSignalEmitter()
        self.gsi_signal_emitter.data_received.connect(self.handle_gsi_data_on_ui_thread)
        self.gsi_manager.register_data_callback(self.handle_gsi_data_from_thread)

        self.update_signal_emitter = UpdateSignalEmitter()
        self.update_signal_emitter.update_found.connect(self.show_update_dialog)

        self.bg_label = QLabel(self)
        self.bg_label.lower()
        self.bg_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.bg_effect = QGraphicsBlurEffect()
        self.bg_label.setGraphicsEffect(self.bg_effect)

        self.init_window()
        self.init_ui()

        self.load_all_presets()
        self.update_home_status()
        self.apply_custom_background()
        # 延迟执行：让窗口先显示，500ms 后再检测 Steam 和检查更新
        QTimer.singleShot(500, self._deferred_startup_tasks)

    def apply_custom_background(self):
        if not hasattr(self, 'config_manager'):
            return
        bg_path = self.config_manager.get("bg_path", "")
        if not bg_path or not os.path.exists(bg_path):
            self.bg_label.hide()
            return

        self.bg_label.show()
        pixmap = QPixmap(bg_path)

        # apply brightness
        bright = self.config_manager.get("bg_bright", 100) / 100.0
        if bright != 1.0:
            img = pixmap.toImage()
            # Simple brightness adjustment
            # Create a painter to overlay black or white with opacity
            painter = QPainter(pixmap)
            painter.setCompositionMode(QPainter.CompositionMode_SourceAtop)
            if bright < 1.0:
                painter.fillRect(pixmap.rect(), QColor(0, 0, 0, int((1 - bright) * 255)))
            else:
                painter.fillRect(pixmap.rect(), QColor(255, 255, 255, int((bright - 1.0) * 255)))
            painter.end()

        # scale and set
        bg_scale = self.config_manager.get("bg_scale", 0)
        # 0: 等比缩放 (KeepAspectRatioByExpanding)
        # 1: 填充 (IgnoreAspectRatio)
        # 2: 拉伸 (IgnoreAspectRatio)
        # 3: 居中 (KeepAspectRatio)

        aspect_ratio_mode = Qt.KeepAspectRatioByExpanding
        if bg_scale == 1 or bg_scale == 2:
            aspect_ratio_mode = Qt.IgnoreAspectRatio
        elif bg_scale == 3:
            aspect_ratio_mode = Qt.KeepAspectRatio

        scaled_pixmap = pixmap.scaled(self.size(), aspect_ratio_mode, Qt.SmoothTransformation)

        # 对于居中模式，我们需要创建一个与窗口一样大且透明的 QPixmap，然后将图片画在中间
        if bg_scale == 3:
            final_pixmap = QPixmap(self.size())
            final_pixmap.fill(Qt.transparent)
            painter = QPainter(final_pixmap)
            x = (self.width() - scaled_pixmap.width()) // 2
            y = (self.height() - scaled_pixmap.height()) // 2
            painter.drawPixmap(x, y, scaled_pixmap)
            painter.end()
            self.bg_label.setPixmap(final_pixmap)
        else:
            self.bg_label.setPixmap(scaled_pixmap)

        self.bg_label.resize(self.size())

        # apply blur
        blur_radius = self.config_manager.get("bg_blur", 0)
        self.bg_effect.setBlurRadius(blur_radius)

        self.bg_label.lower()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_custom_background()

    def _deferred_startup_tasks(self):
        self.auto_detect_steam()
        self.check_for_updates()

        # 启动 GSI Server（路径和配置生成已转移到 _perform_steam_detection 和 browse_steam 中处理）
        self.gsi_manager.start_server()
        self._repair_current_font_if_needed(silent=True)

        # 更新设置页的按钮状态
        if hasattr(self, 'setting_tab') and hasattr(self.setting_tab, 'gsi_toggle_btn'):
            if self.gsi_manager.is_running():
                self.setting_tab.gsi_toggle_btn.setText("停止服务")
                self.setting_tab.gsi_toggle_btn.setEnabled(True)
                self.setting_tab.gsi_port_input.setEnabled(False)
            elif self.gsi_manager.is_starting():
                self.setting_tab.gsi_toggle_btn.setText("启动中...")
                self.setting_tab.gsi_toggle_btn.setEnabled(False)
                self.setting_tab.gsi_port_input.setEnabled(False)

    def init_window(self):
        self.setWindowTitle("CS2 工具箱 · 1.5.1-alpha")
        self.setWindowIcon(QIcon(":/app_icon.ico"))
        self.resize(750, 780)
        self.center()

        # 初始化系统托盘
        self.tray_icon = QSystemTrayIcon(QIcon(":/app_icon.ico"), self)
        self.tray_icon.setToolTip("CS2 工具箱")
        self.tray_menu = QMenu(self)

        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self.show_window)
        self.tray_menu.addAction(show_action)

        quit_action = QAction("完全退出", self)
        quit_action.triggered.connect(self.quit_app)
        self.tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.show()

    def show_window(self):
        self.show()
        self.activateWindow()

    def quit_app(self):
        self._force_quit = True
        self.visual_handler.cleanup()
        QApplication.quit()

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_window()

    def init_ui(self):
        from app.ui.pages.customize_page import CustomizePage
        from app.ui.pages.about_page import AboutPage
        from app.ui.pages.game_enhancement_page import GameEnhancementPage
        from app.ui.pages.setting_page import SettingPage
        from app.ui.pages.visual_page import VisualPage
        from app.ui.pages.go_pet_page import GoPetPage

        self.home_tab = HomePage(self)
        self.home_tab.setObjectName("home_tab")

        self.customize_tab = CustomizePage(self)
        self.customize_tab.setObjectName("customize_tab")

        # Expose sub-pages for existing methods
        self.video_tab = self.customize_tab.video_page
        self.sound_tab = self.customize_tab.sound_page
        self.font_tab = self.customize_tab.font_page

        self.about_tab = AboutPage(self)
        self.about_tab.setObjectName("about_tab")

        self.setting_tab = SettingPage(self)
        self.setting_tab.setObjectName("setting_tab")

        self.visual_tab = VisualPage(self.config_manager, self)
        self.visual_tab.setObjectName("visual_tab")

        self.go_pet_tab = GoPetPage(self.config_manager, self)
        self.go_pet_tab.setObjectName("go_pet_tab")

        self.integrated_sound_page = IntegratedSoundPage(self.gsi_manager, self.config_manager, self)
        self.integrated_sound_page.setObjectName("integrated_sound_page")

        self.gsi_tab = self.integrated_sound_page
        self.gsi_page = self.integrated_sound_page
        self.gsi_sound_page = self.integrated_sound_page
        self.game_enhancement_tab = GameEnhancementPage(self.integrated_sound_page, self.visual_tab, self)
        self.game_enhancement_tab.setObjectName("game_enhancement_tab")

        self.addSubInterface(self.home_tab, FIF.HOME, "主页", NavigationItemPosition.TOP)
        self.addSubInterface(self.game_enhancement_tab, FIF.GAME, "游戏增强")
        self.addSubInterface(self.go_pet_tab, FIF.HEART, "桌宠与互动")
        self.addSubInterface(self.customize_tab, FIF.BRUSH, "个性化")
        self.addSubInterface(self.setting_tab, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.about_tab, FIF.INFO, "关于", NavigationItemPosition.BOTTOM)

    def load_all_presets(self):
        self.load_video_presets()
        self.load_font_presets()
        self.load_sound_presets()

    def center(self):
        qr = self.frameGeometry()
        cp = self.screen().availableGeometry().center()
        qr.moveCenter(cp)
        self.move(qr.topLeft())






    def quick_detect_steam(self):
        """Detects CS2 path and updates UI."""
        self.auto_detect_steam()

    def update_recent_activity(self, activity):
        pass

    def update_home_status(self):
        if hasattr(self, 'home_tab') and hasattr(self.home_tab, 'update_status'):
            self.home_tab.update_status()

    def browse_video(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择视频文件", "", "WebM 视频 (*.webm)")
        if path:
            self.video_tab.video_entry.setText(path)

    def load_video_presets(self):
        self.video_tab.preset_list.clear()
        for i, preset in enumerate(self.config_manager.get_presets('video')):
            item = QListWidgetItem(self.video_tab.preset_list)
            widget = VideoPresetWidget(preset, self.apply_preset)
            item.setSizeHint(widget.sizeHint())
            self.video_tab.preset_list.setItemWidget(item, widget)

    def open_preset_folder(self, path):
        folder = os.path.dirname(path)
        if os.path.exists(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            self.show_error("路径不存在", f"文件夹 '{folder}' 不存在。")

    def _request_thumbnail(self, video_path, on_done):
        """使用 QtMultimedia 异步截取视频第一帧作为缩略图（替代 cv2）"""
        import uuid
        from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
        from PySide6.QtCore import QUrl

        player = QMediaPlayer(self)
        sink = QVideoSink(self)
        player.setVideoSink(sink)
        done = []

        def _on_frame_changed(frame):
            if done:
                return
            if not frame.isValid():
                return
            img = frame.toImage()
            if img.isNull():
                return
            done.append(True)
            try:
                path = os.path.join(
                    self.config_manager.get_thumbnail_dir(),
                    f"{uuid.uuid4().hex}.png",
                )
                img.save(path)
                on_done(path)
            except Exception as e:
                print(f"缩略图保存失败: {e}")
                on_done(None)
            finally:
                player.stop()
                player.deleteLater()
                sink.deleteLater()

        sink.videoFrameChanged.connect(_on_frame_changed)
        player.setSource(QUrl.fromLocalFile(video_path))
        player.play()
        # 兜底：2秒后如果没截到帧则放弃
        QTimer.singleShot(2000, lambda: (
            player.stop(), player.deleteLater(), sink.deleteLater(), on_done(None)
        ) if not done else None)

    def save_preset(self):
        video_path = self.video_tab.video_entry.text()
        if not video_path:
            self.show_warning("操作无效", "请先选择一个视频文件。")
            return

        name, ok = self.get_input_dialog("保存预设", "输入预设名称:", f"预设 {len(self.config_manager.get_presets('video')) + 1}")
        if not ok or not name:
            return

        def _on_thumbnail_ready(thumbnail_path):
            if not thumbnail_path:
                self.show_error("保存失败", "无法生成视频缩略图。")
                return
            self.config_manager.add_preset('video', name=name, video_path=video_path, thumbnail_path=thumbnail_path)
            self.load_video_presets()
            self.update_home_status()
            self.update_recent_activity(f"保存视频预设: {name}")
            self.show_success("成功", "视频预设已保存。")

        self._request_thumbnail(video_path, _on_thumbnail_ready)

    def delete_preset_confirm(self, index):
        title = "确认删除"
        video_presets = self.config_manager.get_presets('video')
        content = f"确定要删除预设 '{video_presets[index]['name']}' 吗？"
        if self.show_confirm_dialog(title, content):
            preset_name = video_presets[index]['name']
            self.config_manager.delete_preset('video', index)
            self.load_video_presets()
            self.update_home_status()
            self.update_recent_activity(f"删除视频预设: {preset_name}")

    def apply_preset(self, preset):
        if not os.path.exists(preset["video_path"]):
            self.show_error("文件丢失", "预设的视频文件已不存在。")
            return

        self.video_tab.video_entry.setText(preset["video_path"])

        if self.show_confirm_dialog("确认应用", f"是否立即应用视频预设 '{preset['name']}'？"):
            self.execute_replace()

    def apply_preset_from_item(self, item):
        index = self.video_tab.preset_list.row(item)
        video_presets = self.config_manager.get_presets('video')
        if 0 <= index < len(video_presets):
            self.apply_preset(video_presets[index])

    def auto_detect_steam(self):

        QTimer.singleShot(10, self._perform_steam_detection)

    def _perform_steam_detection(self):
        # 优先使用配置中保存的路径
        saved_steam_path = self.config_manager.get("steam_path")
        if saved_steam_path and os.path.exists(saved_steam_path) and os.path.exists(os.path.join(saved_steam_path, "game", "bin", "win64", "cs2.exe")):
            self.steam_path = saved_steam_path
            self.update_home_status()

            # 如果是优先从配置加载的，也要顺便给 GSI 生成一次配置，防止用户重装了游戏但路径没变
            self.gsi_manager.set_cs2_path(self.steam_path)
            self.gsi_manager.create_gsi_cfg()
            self._repair_current_font_if_needed(silent=True)
            return

        cs2_path = SteamUtils.find_cs2_install_path()
        if cs2_path and os.path.exists(cs2_path):
            self.steam_path = cs2_path
            self.config_manager.set("steam_path", cs2_path)
            self.update_home_status()

            # 检测到新路径后自动生成配置
            self.gsi_manager.set_cs2_path(self.steam_path)
            self.gsi_manager.create_gsi_cfg()
            self._repair_current_font_if_needed(silent=True)

            self.update_recent_activity("自动检测CS2路径成功")
            InfoBar.success("成功", "已自动检测到CS2安装路径。", parent=self, duration=3000)
        else:
            self.steam_path = ""
            self.update_home_status()
            InfoBar.warning("提示", "未检测到CS2，请手动选择CS2安装目录。", parent=self, duration=5000)

    def browse_steam(self):
        path = QFileDialog.getExistingDirectory(self, "选择CS2安装目录")
        if path:
            # Basic validation: check for cs2.exe
            if os.path.exists(os.path.join(path, "game", "bin", "win64", "cs2.exe")):
                self.steam_path = path
                self.config_manager.set("steam_path", path)
                self.update_home_status()

                # 手动选择后也自动生成一次
                self.gsi_manager.set_cs2_path(self.steam_path)
                self.gsi_manager.create_gsi_cfg()
                self._repair_current_font_if_needed(silent=True)

                self.update_recent_activity(f"手动设置CS2路径为: {path}")
            else:
                self.show_error("路径无效", "所选目录不是有效的CS2安装目录。")

    def launch_cs2(self):
        cs2_path = self.steam_path
        if not cs2_path or not os.path.exists(cs2_path):
            self.show_error("路径缺失", "CS2 路径未设置或无效，请先在主页设置。")
            return

        if LaunchManager.is_cs2_running():
            success, message = LaunchManager.close_cs2()
            if success:
                self.show_success("关闭成功", message)
            else:
                self.show_warning("关闭失败", message)
            self.update_home_status()
            return

        use_vulkan = self.config_manager.get("launch_use_vulkan", False)
        success, message = LaunchManager.launch_cs2(cs2_path, use_vulkan=use_vulkan)
        if success:
            self.show_success("启动成功", message)
        else:
            self.show_error("启动失败", message)
        self.update_home_status()

    def execute_replace(self):
        cs2_path = self.steam_path
        video_path = self.video_tab.video_entry.text()

        if not cs2_path or not os.path.exists(cs2_path):
            self.show_error("路径无效", "CS2路径未设置或无效，请先在主页设置。")
            return
        is_valid, msg = PathValidator.validate_video_file(video_path)
        if not is_valid:
            self.show_error("文件无效", msg)
            return

        self.animation_manager.animate_button_click(self.video_tab.execute_btn)

        version_type = "both"
        if self.video_tab.intl_radio.isChecked(): version_type = "intl"
        if self.video_tab.cn_radio.isChecked(): version_type = "cn"

        try:
            steam_library_path = SteamUtils.extract_steam_library_from_cs2_path(cs2_path)
            if not steam_library_path:
                self.show_error("路径转换失败", "无法从CS2路径获取Steam库路径。")
                return
            replacer = VideoReplacer(steam_library_path)
            result = replacer.replace_video(video_path, version_type)
            if result["success"]:
                self.config_manager.set("current_video", os.path.basename(video_path))
                self.config_manager.set("current_video_path", video_path)
                self.update_home_status()
                self.show_success("替换成功", f"开屏动画已成功替换！共处理 {result['replaced_count']} 个文件。")
            else:
                self.show_error("替换失败", result['error'])
        except Exception as e:
            self.show_error("严重错误", str(e))

    def handle_gsi_data_from_thread(self, data_bytes):
        """Receives data from GSI thread and emits a signal to the main thread."""
        try:
            # 尝试多种编码方式解码数据
            data_str = None
            for encoding in ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']:
                try:
                    data_str = data_bytes.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue

            if data_str is None:
                # 如果所有编码都失败，使用错误处理方式
                data_str = data_bytes.decode('utf-8', errors='ignore')

            game_state = json.loads(data_str)
            self.gsi_signal_emitter.data_received.emit(game_state)
        except (json.JSONDecodeError, Exception):
            pass # Ignore malformed data

    def handle_gsi_data_on_ui_thread(self, game_state):
        """Handles GSI data on the main UI thread."""
        # 处理服务器启动失败
        if "error" in game_state and game_state["error"] == "server_start_failed":
             if hasattr(self, 'home_tab'):
                 self.home_tab.status_gsi_server.setText(f"错误: {game_state.get('message', '无法启动服务器')}")
                 self.home_tab.status_gsi_server.setStyleSheet("color: red;")
             if hasattr(self, 'setting_tab') and hasattr(self.setting_tab, 'gsi_toggle_btn'):
                 self.setting_tab.gsi_toggle_btn.setText("启动服务")
                 self.setting_tab.gsi_toggle_btn.setEnabled(True)
                 self.setting_tab.gsi_port_input.setEnabled(True)
             self.show_error("GSI服务启动失败", f"{game_state.get('message', '无法启动服务器')}\n请在设置页检查端口是否被占用，或尝试更改端口。")
             return

        # 处理端口自动更换警告
        if "warning" in game_state and game_state["warning"] == "port_changed":
            old_port = game_state.get("old_port")
            new_port = game_state.get("new_port")
            self.config_manager.set("gsi_port", new_port)
            self.show_warning("GSI端口被占用", f"原端口 {old_port} 被占用，已自动切换至 {new_port}。\n请前往【设置-高级】点击「重新生成GSI配置」并重启游戏，否则音效将失效！", duration=10000)
            if hasattr(self, 'setting_tab') and hasattr(self.setting_tab, 'gsi_port_input'):
                self.setting_tab.gsi_port_input.setText(str(new_port))
            return

        # 处理服务器启动成功
        if "success" in game_state and game_state["success"] == "server_started":
            port = game_state.get("port", "未知")
            host = game_state.get("host", "127.0.0.1")
            if isinstance(port, int):
                self.config_manager.set("gsi_port", port)
            # Keep the game configuration aligned with the bound listener.
            if self.gsi_manager.cs2_path:
                cfg_ok, cfg_message = self.gsi_manager.create_gsi_cfg()
                if not cfg_ok:
                    self.show_warning("GSI配置未更新", cfg_message, duration=10000)
            if hasattr(self, 'home_tab'):
                self.home_tab.status_gsi_server.setText(f"服务运行中...")
                self.home_tab.status_gsi_server.setStyleSheet("color: green;")

            # 更新设置页的UI状态
            if hasattr(self, 'setting_tab') and hasattr(self.setting_tab, 'gsi_toggle_btn'):
                self.setting_tab.gsi_toggle_btn.setText("停止服务")
                self.setting_tab.gsi_toggle_btn.setEnabled(True)
                self.setting_tab.gsi_port_input.setEnabled(False)

            self.show_success("GSI服务启动成功", f"服务已在 {host}:{port} 端口上开启")
            return

        # 处理其他错误
        if "error" in game_state:
            error_type = game_state["error"]
            if error_type == "server_unexpected_error":
                if hasattr(self, 'home_tab'):
                    self.home_tab.status_gsi_server.setText("错误: 服务器发生意外错误")
                    self.home_tab.status_gsi_server.setStyleSheet("color: red;")
                if hasattr(self, 'setting_tab') and hasattr(self.setting_tab, 'gsi_toggle_btn'):
                    self.setting_tab.gsi_toggle_btn.setText("启动服务")
                    self.setting_tab.gsi_toggle_btn.setEnabled(True)
                    self.setting_tab.gsi_port_input.setEnabled(True)
                self.show_error("GSI服务错误", game_state.get('message', '服务器发生意外错误'))
            return

        # 处理正常的游戏状态数据
        if not any(key in game_state for key in ["error", "success"]):
            player = game_state.get('player', {})
            state = player.get('state', {})
            round_info = game_state.get('round', {})
            self.last_game_state_overview = {
                "phase": round_info.get('phase', '未知'),
                "activity": player.get('activity', '未知'),
                "team": player.get('team', '--') or '--',
                "health": state.get('health', '--'),
            }
            if hasattr(self, 'home_tab'):
                self.home_tab.status_gsi_server.setText("服务运行中...")
                self.home_tab.status_gsi_server.setStyleSheet("color: green;")
                self.home_tab.update_gsi_overview()
            # This is where the event matching logic will go
            # Render time-sensitive visuals before audio/event bookkeeping.
            self.visual_handler.process_gsi(game_state)
            self.integrated_sound_page.process_game_state(game_state)
            self.go_pet_manager.process_gsi(game_state)


    def browse_font_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择字体文件", "", "TrueType 字体 (*.ttf)")
        if path:
            is_valid, msg = PathValidator.validate_font_file(path)
            if is_valid:
                self.font_tab.font_entry.setText(path)
                self.font_path = path
                self.update_font_info(path)
            else:
                self.show_error("字体无效", msg)

    def update_font_info(self, font_path):
        if font_path and os.path.exists(font_path):
            font_name = os.path.splitext(os.path.basename(font_path))[0]
            filename = os.path.basename(font_path)
            self.font_tab.font_name_label.setText(f"字体名称: {font_name}")
            self.font_tab.font_filename_label.setText(f"文件名: {filename}")
            pass  # 状态标签已删除
        else:
            self.font_tab.font_name_label.setText("字体名称: 未选择")
            self.font_tab.font_filename_label.setText("文件名: 未选择")
            pass  # 状态标签已删除

    def load_font_presets(self):
        self.font_tab.font_preset_list.clear()
        font_presets = self.config_manager.get_presets('font')
        for i, preset in enumerate(font_presets):
            item = QListWidgetItem(self.font_tab.font_preset_list)
            widget = FontPresetWidget(preset, self.apply_font_preset)
            item.setSizeHint(widget.sizeHint())
            self.font_tab.font_preset_list.setItemWidget(item, widget)

    def save_font_preset(self):
        font_path = self.font_tab.font_entry.text()
        if not font_path or not os.path.exists(font_path):
            self.show_warning("操作无效", "请先选择一个有效的字体文件。")
            return

        name, ok = self.get_input_dialog("保存字体预设", "输入预设名称:")
        if not ok or not name:
            return

        font_presets = self.config_manager.get_presets('font')
        if any(p['name'] == name for p in font_presets):
            self.show_warning("名称重复", "该预设名称已存在。")
            return

        try:
            font_filename = os.path.basename(font_path)
            font_name_extracted = os.path.splitext(font_filename)[0]
            self.config_manager.add_preset('font', name=name, font_path=font_path, font_name=font_name_extracted, font_filename=font_filename)
            self.load_font_presets()
            self.update_home_status()
            self.update_recent_activity(f"保存字体预设: {name}")
            self.show_success("成功", "字体预设已保存。")
        except Exception as e:
            self.show_error("保存失败", str(e))

    def delete_font_preset_confirm(self, index):
        title = "确认删除"
        font_presets = self.config_manager.get_presets('font')
        content = f"确定要删除字体预设 '{font_presets[index]['name']}' 吗？"
        if self.show_confirm_dialog(title, content):
            preset_name = font_presets[index]['name']
            self.config_manager.delete_preset('font', index)
            self.load_font_presets()
            self.update_home_status()
            self.update_recent_activity(f"删除字体预设: {preset_name}")

    def apply_font_preset(self, preset):
        if not os.path.exists(preset["font_path"]):
            self.show_error("文件丢失", "预设的字体文件已不存在。")
            return

        self.font_tab.font_entry.setText(preset["font_path"])
        self.font_path = preset["font_path"]
        self.update_font_info(preset["font_path"])

        if self.show_confirm_dialog("确认应用", f"是否立即应用字体预设 '{preset['name']}'？"):
            self.execute_font_replace()

    def apply_font_preset_from_item(self, item):
        index = self.font_tab.font_preset_list.row(item)
        font_presets = self.config_manager.get_presets('font')
        if 0 <= index < len(font_presets):
            self.apply_font_preset(font_presets[index])

    def _repair_current_font_if_needed(self, silent=True):
        current_font_path = self.config_manager.get("current_font_path")
        if not current_font_path or not os.path.exists(current_font_path):
            return False
        if not self.steam_path or not os.path.exists(self.steam_path):
            return False

        steam_library_path = SteamUtils.extract_steam_library_from_cs2_path(self.steam_path)
        if not steam_library_path:
            return False

        try:
            replacer = FontReplacer(steam_library_path)
            result = replacer.ensure_font_replaced(current_font_path, lambda msg: None)
            if not result.get("success"):
                if not silent:
                    self.show_warning("字体检查失败", result.get("error", "当前字体状态检查失败。"))
                return False

            self.font_path = current_font_path
            if result.get("repaired"):
                font_name = result.get("font_name") or os.path.splitext(os.path.basename(current_font_path))[0]
                self.config_manager.set("current_font", font_name)
                self.config_manager.set("current_font_path", current_font_path)
                self.update_home_status()
                if not silent:
                    self.show_success("字体已恢复", f"已自动补齐字体文件：{font_name}")
                return True
        except Exception as e:
            print(f"字体自愈失败: {e}")
        return False

    def execute_font_replace(self):
        if not self.font_path or not os.path.exists(self.font_path):
            self.show_error("文件无效", "请先选择有效的字体文件。")
            return
        cs2_path = self.steam_path
        if not cs2_path or not os.path.exists(cs2_path):
            self.show_error("路径缺失", "CS2路径未设置或无效，请先在主页设置。")
            return

        self.animation_manager.animate_button_click(self.font_tab.execute_font_btn)

        try:
            steam_library_path = SteamUtils.extract_steam_library_from_cs2_path(cs2_path)
            if not steam_library_path:
                self.show_error("路径转换失败", "无法从CS2路径获取Steam库路径。")
                return
            replacer = FontReplacer(steam_library_path)
            QApplication.processEvents()

            result = replacer.replace_font(self.font_path, lambda msg: None)

            if result["success"]:
                self.config_manager.set("current_font", result['font_name'])
                self.config_manager.set("current_font_path", self.font_path)
                self.update_home_status()
                self.show_success("替换成功", f"字体已成功替换为 {result['font_name']}。")
            else:
                self.show_error("替换失败", result['error'])
        except Exception as e:
            self.show_error("严重错误", str(e))
        finally:
            pass  # 状态标签已删除

    def show_info(self, title, content, duration=3000):
        InfoBar.info(title, content, parent=self, duration=duration)

    def show_success(self, title, content, duration=3000):
        InfoBar.success(title, content, parent=self, duration=duration)

    def show_warning(self, title, content, duration=5000):
        InfoBar.warning(title, content, parent=self, duration=duration)

    def show_error(self, title, content, duration=5000):
        InfoBar.error(title, content, parent=self, duration=duration)

    def show_confirm_dialog(self, title, content):
        dialog = MessageBox(title, content, self)
        return dialog.exec()

    def get_input_dialog(self, title, content, default_text=""):
        dialog = TextInputDialog(title, content, default_text, self)
        if dialog.exec():
            return dialog.get_text(), True
        return "", False

    def browse_sound_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择音效文件", "", "音效文件 (*.vsnd_c)")
        if path:
            self.sound_tab.sound_entry.setText(path)
            self.sound_path = path
            self.update_sound_info(path)

    def execute_sound_replace(self):
        if not self.sound_path or not os.path.exists(self.sound_path):
            self.show_error("文件无效", "请先选择有效的音效文件。")
            return
        cs2_path = self.steam_path
        if not cs2_path or not os.path.exists(cs2_path):
            self.show_error("路径缺失", "CS2路径未设置或无效，请先在主页设置。")
            return

        self.animation_manager.animate_button_click(self.sound_tab.execute_btn)

        try:
            replacer = SoundReplacer(cs2_path)
            result = replacer.replace_sound(self.sound_path)

            if result["success"]:
                self.config_manager.set("current_sound", result['sound_name'])
                self.config_manager.set("current_sound_path", self.sound_path)
                self.update_home_status()
                self.show_success("替换成功", f"启动音效已成功替换为 {result['sound_name']}。")
            else:
                self.show_error("替换失败", result['error'])
        except Exception as e:
            self.show_error("严重错误", str(e))

    def update_sound_info(self, sound_path):
        if sound_path and os.path.exists(sound_path):
            sound_name = os.path.splitext(os.path.basename(sound_path))[0]
            filename = os.path.basename(sound_path)
            self.sound_tab.sound_name_label.setText(f"音效名称: {sound_name}")
            self.sound_tab.sound_filename_label.setText(f"文件名: {filename}")
            pass  # 状态标签已删除
        else:
            self.sound_tab.sound_name_label.setText("音效名称: 未选择")
            self.sound_tab.sound_filename_label.setText("文件名: 未选择")
            pass  # 状态标签已删除

    def restore_sound(self):
        cs2_path = self.steam_path
        if not cs2_path or not os.path.exists(cs2_path):
            self.show_error("路径缺失", "CS2路径未设置或无效，请先在主页设置。")
            return

        self.animation_manager.animate_button_click(self.sound_tab.restore_btn)

        try:
            replacer = SoundReplacer(cs2_path)
            result = replacer.restore_sound()

            if result["success"]:
                self.update_recent_activity("还原默认启动音效")
                self.show_success("还原成功", result["message"])
            else:
                self.show_error("还原失败", result["error"])
        except Exception as e:
            self.show_error("严重错误", str(e))

    def load_sound_presets(self):
        self.sound_tab.sound_preset_list.clear()
        sound_presets = self.config_manager.get_presets('sound')
        for i, preset in enumerate(sound_presets):
            item = QListWidgetItem(self.sound_tab.sound_preset_list)
            widget = SoundPresetWidget(preset, self.apply_sound_preset)
            item.setSizeHint(widget.sizeHint())
            self.sound_tab.sound_preset_list.setItemWidget(item, widget)

    def save_sound_preset(self):
        sound_path = self.sound_tab.sound_entry.text()
        if not sound_path or not os.path.exists(sound_path):
            self.show_warning("操作无效", "请先选择一个有效的音效文件。")
            return

        name, ok = self.get_input_dialog("保存音效预设", "输入预设名称:")
        if not ok or not name:
            return

        sound_presets = self.config_manager.get_presets('sound')
        if any(p['name'] == name for p in sound_presets):
            self.show_warning("名称重复", "该预设名称已存在。")
            return

        try:
            sound_filename = os.path.basename(sound_path)
            sound_name_extracted = os.path.splitext(sound_filename)[0]
            self.config_manager.add_preset('sound', name=name, sound_path=sound_path, sound_name=sound_name_extracted, sound_filename=sound_filename)
            self.load_sound_presets()
            self.update_home_status()
            self.update_recent_activity(f"保存音效预设: {name}")
            self.show_success("成功", "音效预设已保存。")
        except Exception as e:
            self.show_error("保存失败", str(e))

    def delete_sound_preset_confirm(self, index):
        title = "确认删除"
        sound_presets = self.config_manager.get_presets('sound')
        content = f"确定要删除音效预设 '{sound_presets[index]['name']}' 吗？"
        if self.show_confirm_dialog(title, content):
            preset_name = sound_presets[index]['name']
            self.config_manager.delete_preset('sound', index)
            self.load_sound_presets()
            self.update_home_status()
            self.update_recent_activity(f"删除音效预设: {preset_name}")

    def apply_sound_preset(self, preset):
        if not os.path.exists(preset["sound_path"]):
            self.show_error("文件丢失", "预设的音效文件已不存在。")
            return

        self.sound_tab.sound_entry.setText(preset["sound_path"])
        self.sound_path = preset["sound_path"]
        self.update_sound_info(preset["sound_path"])

        if self.show_confirm_dialog("确认应用", f"是否立即应用音效预设 '{preset['name']}'？"):
            self.execute_sound_replace()

    def apply_sound_preset_from_item(self, item):
        index = self.sound_tab.sound_preset_list.row(item)
        sound_presets = self.config_manager.get_presets('sound')
        if 0 <= index < len(sound_presets):
            self.apply_sound_preset(sound_presets[index])

    def open_sound_tutorial(self):
        class TutorialDialog(MessageBoxBase):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.titleLabel = SubtitleLabel('替换教程', self)
                self.viewLayout.addWidget(self.titleLabel)

                self.textLabel = BodyLabel(self)
                self.textLabel.setOpenExternalLinks(True)
                self.textLabel.setTextFormat(Qt.RichText)
                self.textLabel.setText(
                    "<h3>准备工作</h3>"
                    "<p>准备好wav格式的音频文件，并使用转换软件将其转换为vsnd_c格式。<br/>"
                    "转换软件下载地址：<a href='https://github.com/quad-damage/CS2_Sound_Converter'>CS2_Sound_Converter</a></p>"
                    "<p>转换软件使用教程：打开下载的exe文件，将准备好的wav格式音频文件拖入，软件会自动将转换好的文件生成于wav音频文件所在的文件夹下。</p>"
                    "<h3>替换启动音效</h3>"
                    "<ol>"
                    "<li>打开CS2 Toolkit</li>"
                    "<li>点击“个性化替换” -> “启动音效”选项卡</li>"
                    "<li>选择VSND_C文件，点击“替换启动音效”</li>"
                    "<li>替换完成！</li>"
                    "</ol>"
                )
                self.textLabel.setWordWrap(True)
                self.viewLayout.addWidget(self.textLabel)
                self.widget.setMinimumWidth(450)

                self.yesButton.setText('我知道了')
                self.cancelButton.hide()

        dialog = TutorialDialog(self)
        dialog.exec()
        self.update_recent_activity("查看音效替换教程")

    def check_for_updates(self):
        thread = threading.Thread(target=self._update_check_thread, daemon=True)
        thread.start()

    def _update_check_thread(self):
        import requests
        current_version = self.version
        try:
            proxies = {
              "http": None,
              "https": None,
            }
            response = requests.get(UPDATE_URL, timeout=5, proxies=proxies)
            if response.status_code == 200:
                latest_version_data = response.json()
                latest_version = latest_version_data.get("version")
                if latest_version and latest_version != current_version:
                    self.update_signal_emitter.update_found.emit(latest_version_data)
                elif latest_version and latest_version == current_version:
                    self.update_signal_emitter.update_found.emit({"version": latest_version, "is_latest": True})
        except requests.RequestException as e:
            print(f"检查更新失败: {e}")

    def show_update_dialog(self, version_data):
        # json文件格式
        # {
        #   "version": "最新的版本号(✪ω✪)",
        #   "update_log": "更新日志的具体说明(*^▽^*)",
        #   "download_url": "下载链接o(´^｀)o"
        # }
        if version_data.get("is_latest", False):
            self.show_info("检查更新", "当前已是最新版本")
            return
        latest_version = version_data.get("version", "N/A")
        update_log = version_data.get("update_log", "无更新日志。")
        download_url = version_data.get("download_url", "")

        title = f"发现新版本: {latest_version}"
        content = f"检测到新版本，是否立即更新？\n\n更新日志:\n{update_log}"

        msg_box = MessageBox(title, content, self)
        msg_box.yesButton.setText("立即更新")
        msg_box.cancelButton.setText("忽略此版本")

        if msg_box.exec():
            if download_url:
                QDesktopServices.openUrl(QUrl(download_url))
        else:
            self.show_warning("已忽略更新", "您拒绝更新到最新版本，在此版本中遇到任何问题请勿向作者报告！")

    def closeEvent(self, event):
        if self._force_quit:
            self.visual_handler.cleanup()
            self.gsi_manager.stop_server()
            super().closeEvent(event)
            return

        hide_prompt = self.config_manager.get("hide_close_prompt", False)
        behavior = self.config_manager.get("close_behavior", "prompt")

        if not hide_prompt and behavior == "prompt":
            msg_box = MessageBox("退出程序", "您想如何处理关闭操作？", self)
            msg_box.yesButton.setText("最小化到托盘")
            msg_box.cancelButton.setText("完全退出")

            # 找到 MessageBox 中的 textLayout 或直接向 widget() 的 layout 中添加
            checkbox = CheckBox("不再提示", msg_box.widget)
            msg_box.textLayout.addWidget(checkbox)

            if msg_box.exec():
                if checkbox.isChecked():
                    self.config_manager.set("hide_close_prompt", True)
                    self.config_manager.set("close_behavior", "tray")
                    if hasattr(self, 'setting_tab'):
                        self.setting_tab.closeCard.comboBox.setCurrentIndex(1)
                self.hide()
                event.ignore()
            else:
                if checkbox.isChecked():
                    self.config_manager.set("hide_close_prompt", True)
                    self.config_manager.set("close_behavior", "exit")
                    if hasattr(self, 'setting_tab'):
                        self.setting_tab.closeCard.comboBox.setCurrentIndex(2)
                self.visual_handler.cleanup()
                self.gsi_manager.stop_server()
                super().closeEvent(event)
        elif behavior == "tray":
            self.hide()
            event.ignore()
        else:
            self.visual_handler.cleanup()
            self.gsi_manager.stop_server()
            super().closeEvent(event)

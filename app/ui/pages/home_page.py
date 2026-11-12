import threading

import requests
from PySide6.QtCore import Qt, Signal, QTimer, Slot
from PySide6.QtWidgets import QFormLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    PrimaryPushButton,
    ScrollArea,
    SettingCard,
    SettingCardGroup,
    SubtitleLabel,
    SwitchSettingCard,
    PushButton,
    SimpleCardWidget,
    TitleLabel,
    isDarkTheme,
)

from app.release_endpoints import ANNOUNCEMENT_URL
from app.logic.launch_manager import LaunchManager
from ..styles import UIStyles


class HomePage(ScrollArea):
    announcement_fetched = Signal(str, str)
    game_running_checked = Signal(bool)

    def __init__(self, parent):
        super().__init__(parent=parent)
        self.parent_window = parent
        self.section_widgets = {}
        self._game_running = False
        self._process_probe_pending = False
        self.game_running_checked.connect(self._set_game_running, Qt.QueuedConnection)

        self.view = QWidget(self)
        self.view.setObjectName("home_view")
        self.layout = QVBoxLayout(self.view)
        self.layout.setContentsMargins(30, 20, 30, 30)
        self.layout.setSpacing(20)

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setObjectName("homePage")
        self.setStyleSheet("QScrollArea {background: transparent; border: none;} #home_view {background: transparent;}")

        self.announcement_fetched.connect(self._update_announcement_ui)
        self._setup_ui()
        self._fetch_announcement()
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._refresh_status)
        self.status_timer.start(2000)
        QTimer.singleShot(0, self._refresh_status)

    def _setup_ui(self):
        self.section_widgets = {
            "header": self._build_header_section(),
            "announcement": self._build_announcement_card(),
            "controls": self._build_controls_card(),
            "status": self._build_status_card(),
        }
        for key in ("header", "announcement", "controls", "status"):
            self.layout.addWidget(self.section_widgets[key])
        self.layout.addStretch()
        self.update_status()

    def _create_card(self):
        card = SimpleCardWidget(self.view)
        UIStyles.apply_styles(card)
        card.setObjectName("actionCard")
        return card

    def _create_translucent_card(self, object_name):
        card = SimpleCardWidget(self.view)
        card.setObjectName(object_name)
        self._apply_translucent_card_style(card)
        return card

    def _apply_translucent_card_style(self, card):
        if isDarkTheme():
            bg = "rgba(255, 255, 255, 0.06)"
            border = "rgba(255, 255, 255, 0.10)"
            hover_bg = "rgba(255, 255, 255, 0.09)"
            hover_border = "rgba(0, 120, 212, 0.45)"
        else:
            bg = "rgba(255, 255, 255, 0.28)"
            border = "rgba(0, 0, 0, 0.08)"
            hover_bg = "rgba(255, 255, 255, 0.38)"
            hover_border = "rgba(0, 120, 212, 0.35)"

        card.setStyleSheet(f"""
            SimpleCardWidget#{card.objectName()} {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            SimpleCardWidget#{card.objectName()}:hover {{
                background-color: {hover_bg};
                border: 1px solid {hover_border};
            }}
        """)

    def _create_action_card(self, icon, title, content, button_text, button_slot, parent):
        card = SettingCard(icon, title, content, parent)
        button = PrimaryPushButton(button_text, card)
        button.clicked.connect(button_slot)
        card.hBoxLayout.addWidget(button, 0, Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addSpacing(8)
        return card, button

    def _build_header_section(self):
        header_widget = QWidget(self.view)
        layout = QVBoxLayout(header_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(TitleLabel("主页", header_widget))
        return header_widget

    def _build_announcement_card(self):
        self.announcement_card = self._create_translucent_card("announcementCard")
        announcement_layout = QVBoxLayout(self.announcement_card)
        announcement_layout.setContentsMargins(20, 20, 20, 20)
        announcement_layout.setSpacing(10)

        self.announcement_title = SubtitleLabel("公告栏", self.announcement_card)
        self.announcement_content = BodyLabel("正在获取最新公告...", self.announcement_card)
        self.announcement_content.setWordWrap(True)
        self.announcement_content.setTextFormat(Qt.RichText)
        self.announcement_content.setOpenExternalLinks(True)

        announcement_layout.addWidget(self.announcement_title)
        announcement_layout.addWidget(self.announcement_content)
        return self.announcement_card

    def _build_controls_card(self):
        controls_widget = QWidget(self.view)
        layout = QVBoxLayout(controls_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        self.game_group = SettingCardGroup("快捷控制", controls_widget)
        self.launch_card, self.launch_cs2_btn = self._create_action_card(
            FluentIcon.GAME,
            "启动 CS2",
            "快速启动或关闭当前运行中的 CS2。",
            "启动 CS2",
            self.parent_window.launch_cs2,
            self.game_group
        )
        self.game_group.addSettingCard(self.launch_card)

        self.browse_path_card = SettingCard(
            FluentIcon.FOLDER,
            "CS2 路径",
            "手动选择 CS2 安装目录。",
            self.game_group
        )
        self.browse_steam_btn = PushButton("选择路径", self.browse_path_card)
        self.browse_steam_btn.clicked.connect(self.parent_window.browse_steam)
        self.browse_path_card.hBoxLayout.addWidget(self.browse_steam_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.browse_path_card.hBoxLayout.addSpacing(8)
        self.game_group.addSettingCard(self.browse_path_card)

        self.game_sound_card = SwitchSettingCard(
            icon=FluentIcon.HEADPHONE,
            title="游戏音效开关",
            content="控制游戏内音效事件是否参与匹配与播放。",
            parent=self.game_group
        )
        self.game_sound_card.switchButton.checkedChanged.connect(self._on_gsi_enabled_toggled)
        self.game_group.addSettingCard(self.game_sound_card)

        self.visual_master_card = SwitchSettingCard(
            icon=FluentIcon.VIEW,
            title="游戏视觉效果开关",
            content="控制闪光覆盖、死亡画面、击杀图标和死后切屏总开关。",
            parent=self.game_group
        )
        self.visual_master_card.switchButton.checkedChanged.connect(self._on_visual_master_toggled)
        self.game_group.addSettingCard(self.visual_master_card)
        self.go_pet_card = SwitchSettingCard(
            icon=FluentIcon.HEART,
            title="桌宠显示开关",
            content="控制 GO 桌宠显示或隐藏。",
            parent=self.game_group
        )
        self.go_pet_card.switchButton.checkedChanged.connect(self._on_go_pet_toggled)
        self.game_group.addSettingCard(self.go_pet_card)
        layout.addWidget(self.game_group)
        return controls_widget

    def _build_status_card(self):
        status_card = self._create_translucent_card("statusCard")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(20, 20, 20, 20)
        status_layout.setSpacing(15)

        status_title = SubtitleLabel("当前状态", status_card)
        status_layout.addWidget(status_title)

        form_layout = QFormLayout()
        form_layout.setSpacing(10)

        self.steam_status_label = BodyLabel("未检测", status_card)
        self.status_gsi_server = BodyLabel("未知", status_card)
        self.video_status_label = BodyLabel("未知", status_card)
        self.sound_status_label = BodyLabel("未知", status_card)
        self.font_status_label = BodyLabel("未知", status_card)
        self.gsi_count_label = BodyLabel("0", status_card)
        self.game_phase_label = BodyLabel("等待 GSI", status_card)
        self.game_activity_label = BodyLabel("未连接", status_card)
        self.game_team_label = BodyLabel("--", status_card)
        self.game_health_label = BodyLabel("--", status_card)

        form_layout.addRow(BodyLabel("CS2路径:"), self.steam_status_label)
        form_layout.addRow(BodyLabel("GSI连接状态:"), self.status_gsi_server)
        form_layout.addRow(BodyLabel("当前回合阶段:"), self.game_phase_label)
        form_layout.addRow(BodyLabel("当前游戏状态:"), self.game_activity_label)
        form_layout.addRow(BodyLabel("当前阵营:"), self.game_team_label)
        form_layout.addRow(BodyLabel("当前生命值:"), self.game_health_label)
        form_layout.addRow(BodyLabel("当前开屏动画:"), self.video_status_label)
        form_layout.addRow(BodyLabel("当前启动音效:"), self.sound_status_label)
        form_layout.addRow(BodyLabel("当前字体:"), self.font_status_label)
        form_layout.addRow(BodyLabel("设定游戏内音效数:"), self.gsi_count_label)

        status_layout.addLayout(form_layout)
        return status_card

    def update_gsi_overview(self):
        overview = getattr(self.parent_window, "last_game_state_overview", {})
        self.game_phase_label.setText(str(overview.get("phase", "等待 GSI")))
        self.game_activity_label.setText(str(overview.get("activity", "未连接")))
        self.game_team_label.setText(str(overview.get("team", "--")))
        self.game_health_label.setText(str(overview.get("health", "--")))

    def _refresh_status(self):
        self.update_status()
        if self._process_probe_pending:
            return
        self._process_probe_pending = True

        def probe():
            try:
                running = LaunchManager.is_cs2_running()
            except Exception:
                running = False
            try:
                self.game_running_checked.emit(running)
            except RuntimeError:
                pass  # The page may have been destroyed while the worker ran.

        threading.Thread(target=probe, daemon=True).start()

    @Slot(bool)
    def _set_game_running(self, running):
        self._game_running = running
        self._process_probe_pending = False
        self.update_status()

    def update_status(self):
        if not self.parent_window:
            return

        steam_path = self.parent_window.steam_path
        if steam_path:
            self.steam_status_label.setText(steam_path)
            self.steam_status_label.setStyleSheet("color: green;")
            running = self._game_running
            if running:
                self.launch_cs2_btn.setText("关闭 CS2")
                self.launch_card.titleLabel.setText("关闭 CS2")
                self.launch_card.setContent("已检测到正在运行的 CS2，可直接从主页关闭游戏。")
            else:
                self.launch_cs2_btn.setText("启动 CS2")
                self.launch_card.titleLabel.setText("启动 CS2")
                self.launch_card.setContent("已检测到 CS2 路径，可直接通过主页快速启动。")
        else:
            self.steam_status_label.setText("未检测到")
            self.steam_status_label.setStyleSheet("color: red;")
            self.launch_cs2_btn.setText("启动 CS2")
            self.launch_card.titleLabel.setText("启动 CS2")
            self.launch_card.setContent("尚未检测到 CS2 路径，请先手动选择。")

        if hasattr(self.parent_window, 'gsi_manager') and self.parent_window.gsi_manager.is_running():
            self.status_gsi_server.setText("运行中")
            self.status_gsi_server.setStyleSheet("color: green;")
        else:
            self.status_gsi_server.setText("未运行")
            self.status_gsi_server.setStyleSheet("color: red;")

        self.video_status_label.setText(self.parent_window.config_manager.get("current_video", "未知"))
        self.sound_status_label.setText(self.parent_window.config_manager.get("current_sound", "未知"))
        self.font_status_label.setText(self.parent_window.config_manager.get("current_font", "未知"))

        events = self.parent_window.config_manager.get("gsi_events", [])
        self.gsi_count_label.setText(f"{len(events)} 个")

        self.update_gsi_overview()

        self.game_sound_card.switchButton.blockSignals(True)
        self.game_sound_card.switchButton.setChecked(self.parent_window.config_manager.get("gsi_enabled", True))
        self.game_sound_card.switchButton.blockSignals(False)

        visual_config = self.parent_window.config_manager.get("visual", {})
        self.visual_master_card.switchButton.blockSignals(True)
        self.visual_master_card.switchButton.setChecked(visual_config.get("visual_master_enabled", True))
        self.visual_master_card.switchButton.blockSignals(False)

        go_pet_config = self.parent_window.config_manager.get("go_pet", {})
        self.go_pet_card.switchButton.blockSignals(True)
        self.go_pet_card.switchButton.setChecked(go_pet_config.get("enabled", False))
        self.go_pet_card.switchButton.blockSignals(False)

    def _on_gsi_enabled_toggled(self, is_checked):
        self.parent_window.config_manager.set("gsi_enabled", is_checked)
        self.update_status()

    def _on_visual_master_toggled(self, is_checked):
        visual_config = dict(self.parent_window.config_manager.get("visual", {}))
        visual_config["visual_master_enabled"] = is_checked
        self.parent_window.config_manager.set("visual", visual_config)
        if hasattr(self.parent_window, "visual_handler"):
            self.parent_window.visual_handler._update_config(force=True)
        self.update_status()

    def _on_go_pet_toggled(self, is_checked):
        go_pet_config = dict(self.parent_window.config_manager.get("go_pet", {}))
        go_pet_config["enabled"] = is_checked
        self.parent_window.config_manager.set("go_pet", go_pet_config)

        if hasattr(self.parent_window, "go_pet_tab"):
            self.parent_window.go_pet_tab.pet_config = go_pet_config
            self.parent_window.go_pet_tab.enable_card.switchButton.blockSignals(True)
            self.parent_window.go_pet_tab.enable_card.switchButton.setChecked(is_checked)
            self.parent_window.go_pet_tab.enable_card.switchButton.blockSignals(False)
            self.parent_window.go_pet_tab._sync_display_mode_ui()

        if hasattr(self.parent_window, "go_pet_manager"):
            self.parent_window.go_pet_manager.set_enabled(is_checked)
        self.update_status()

    def _fetch_announcement(self):
        def fetch_task():
            try:
                proxies = {"http": None, "https": None}
                response = requests.get(ANNOUNCEMENT_URL, timeout=5, proxies=proxies)
                if response.status_code == 200:
                    data = response.json()
                    title = data.get("title", "最新公告")
                    content = data.get("content", "暂无公告内容。")
                    self.announcement_fetched.emit(title, content)
                else:
                    self.announcement_fetched.emit("公告栏", "暂无最新公告。")
            except Exception:
                self.announcement_fetched.emit("公告栏", "获取公告失败。")

        threading.Thread(target=fetch_task, daemon=True).start()

    def _update_announcement_ui(self, title, content):
        self.announcement_title.setText(title)
        if "<" not in content and ">" not in content:
            content = content.replace("\n", "<br>")
        self.announcement_content.setText(content)

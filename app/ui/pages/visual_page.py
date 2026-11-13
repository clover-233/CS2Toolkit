import os
from ...logic.flash_background import flash_image_extensions
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFileDialog, QColorDialog
from PySide6.QtCore import Qt, QTimer
from qfluentwidgets import (ScrollArea, SettingCardGroup, SwitchSettingCard, SettingCard,
                           PushSettingCard, PushButton, FluentIcon as FIF, LineEdit, MessageBoxBase, SubtitleLabel, BodyLabel, SwitchButton, Slider, InfoBar)

class VisualSettingCard(SettingCard):
    def __init__(self, icon, title, content=None, parent=None):
        super().__init__(icon, title, content, parent)

        self.setting_btn = PushButton("设置", self)
        self.switch_btn = SwitchButton("关", self)
        self.switch_btn.setOnText("开")
        self.switch_btn.setOffText("关")

        self.hBoxLayout.addWidget(self.setting_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)
        self.hBoxLayout.addWidget(self.switch_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

class VisualConfigDialog(MessageBoxBase):
    def __init__(self, config_manager, dialog_type, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.dialog_type = dialog_type # 'flash', 'death', 'boss_key'
        self.visual_config = self.config_manager.get('visual', {})
        self._setup_ui()

    def _setup_ui(self):
        if self.dialog_type == 'flash':
            self.titleLabel = SubtitleLabel('自定义闪光图片', self)
            self.viewLayout.addWidget(self.titleLabel)

            # File selection
            selection_layout = QHBoxLayout()
            selection_layout.addWidget(BodyLabel("图片路径:"))

            path_val = self.visual_config.get('flash_path', '')
            self.path_label = BodyLabel(os.path.basename(path_val) if path_val else "未选择")
            self.path_label.setStyleSheet("color: #666666;")
            selection_layout.addWidget(self.path_label, 1)

            self.select_btn = PushButton("选择图片", self)
            self.select_btn.clicked.connect(self._browse_flash_image)
            selection_layout.addWidget(self.select_btn)

            self.select_dir_btn = PushButton("选择文件夹", self)
            self.select_dir_btn.clicked.connect(self._browse_flash_dir)
            selection_layout.addWidget(self.select_dir_btn)

            self.viewLayout.addLayout(selection_layout)

            # Scale mode selection
            scale_layout = QHBoxLayout()
            scale_layout.addWidget(BodyLabel("缩放模式:"))
            from qfluentwidgets import ComboBox
            self.scale_combo = ComboBox(self)
            self.scale_combo.addItems(["拉伸拉满 (忽略比例)", "保持比例拉满 (可能裁剪)", "保持比例完整 (可能显示不全)"])

            scale_val = self.visual_config.get('flash_scale_mode', 'stretch')
            if scale_val == 'stretch':
                idx = 0
            elif scale_val == 'keep_aspect_crop':
                idx = 1
            else:
                idx = 2
            self.scale_combo.setCurrentIndex(idx)
            self.scale_combo.currentIndexChanged.connect(self._on_flash_scale_changed)
            scale_layout.addWidget(self.scale_combo, 1)
            self.viewLayout.addLayout(scale_layout)

            background_layout = QHBoxLayout()
            background_layout.addWidget(BodyLabel("半透明图片显示原画面:"))
            self.flash_background_switch = SwitchButton(self)
            self.flash_background_switch.setOnText("开")
            self.flash_background_switch.setOffText("关")
            self.flash_background_switch.setChecked(self.visual_config.get('flash_freeze_background_enabled', True))
            self.flash_background_switch.checkedChanged.connect(self._on_flash_background_changed)
            background_layout.addStretch()
            background_layout.addWidget(self.flash_background_switch)
            self.viewLayout.addLayout(background_layout)
            background_hint = BodyLabel("开启：透明区域显示闪光前的静止画面；关闭：普通透明叠加。")
            background_hint.setWordWrap(True)
            self.viewLayout.addWidget(background_hint)

            capture_layout = QHBoxLayout()
            capture_layout.addWidget(BodyLabel("原画面捕获:"))
            self.capture_combo = ComboBox(self)
            self.capture_combo.addItems(["低占用（6 帧/秒）", "兼容（12 帧/秒，占用较高）"])
            self.capture_combo.setCurrentIndex(1 if self.visual_config.get('flash_capture_mode') == 'compat' else 0)
            self.capture_combo.currentIndexChanged.connect(self._on_capture_mode_changed)
            capture_layout.addWidget(self.capture_combo, 1)
            self.viewLayout.addLayout(capture_layout)
            self.viewLayout.addWidget(BodyLabel("原画面异常时可切换兼容模式。"))

            reduce_layout = QHBoxLayout()
            reduce_layout.addWidget(BodyLabel("闪光弹降噪:"))
            self.flash_audio_switch = SwitchButton(self)
            self.flash_audio_switch.setOnText("开")
            self.flash_audio_switch.setOffText("关")
            self.flash_audio_switch.setChecked(self.visual_config.get('flash_reduce_volume_enabled', False))
            self.flash_audio_switch.checkedChanged.connect(self._on_flash_audio_switch_changed)
            reduce_layout.addStretch()
            reduce_layout.addWidget(self.flash_audio_switch)
            self.viewLayout.addLayout(reduce_layout)

            reduce_slider_layout = QHBoxLayout()
            reduce_slider_layout.addWidget(BodyLabel("闪后保留音量:"))
            self.flash_audio_slider = Slider(Qt.Horizontal, self)
            self.flash_audio_slider.setRange(0, 100)
            self.flash_audio_slider.setValue(self.visual_config.get('flash_reduce_volume_percent', 25))
            self.flash_audio_slider.valueChanged.connect(self._on_flash_audio_percent_changed)
            self.flash_audio_value = BodyLabel(f"{self.flash_audio_slider.value()}%")
            reduce_slider_layout.addWidget(self.flash_audio_slider, 1)
            reduce_slider_layout.addWidget(self.flash_audio_value)
            self.viewLayout.addLayout(reduce_slider_layout)

        elif self.dialog_type == 'death':
            self.titleLabel = SubtitleLabel('自定义死亡画面', self)
            self.viewLayout.addWidget(self.titleLabel)
            self.viewLayout.addWidget(BodyLabel("提示：PNG 图片支持透明/半透明显示。"))

            selection_layout = QHBoxLayout()
            selection_layout.addWidget(BodyLabel("媒体路径:"))

            path_val = self.visual_config.get('death_media_path', '')
            self.path_label = BodyLabel(os.path.basename(path_val) if path_val else "未选择")
            self.path_label.setStyleSheet("color: #666666;")
            selection_layout.addWidget(self.path_label, 1)

            self.select_btn = PushButton("选择文件", self)
            self.select_btn.clicked.connect(self._browse_death_media)
            selection_layout.addWidget(self.select_btn)
            self.viewLayout.addLayout(selection_layout)

            # Scale mode selection
            scale_layout = QHBoxLayout()
            scale_layout.addWidget(BodyLabel("图片缩放模式:"))
            from qfluentwidgets import ComboBox
            self.death_scale_combo = ComboBox(self)
            self.death_scale_combo.addItems(["拉伸拉满 (忽略比例)", "保持比例拉满 (可能裁剪)", "保持比例完整 (可能显示不全)"])

            scale_val = self.visual_config.get('death_scale_mode', 'stretch')
            if scale_val == 'stretch':
                idx = 0
            elif scale_val == 'keep_aspect_crop':
                idx = 1
            else:
                idx = 2
            self.death_scale_combo.setCurrentIndex(idx)
            self.death_scale_combo.currentIndexChanged.connect(self._on_death_scale_changed)
            scale_layout.addWidget(self.death_scale_combo, 1)
            self.viewLayout.addLayout(scale_layout)

        elif self.dialog_type == 'boss_key':
            self.titleLabel = SubtitleLabel('死后自动打开内容', self)
            self.viewLayout.addWidget(self.titleLabel)

            type_layout = QHBoxLayout()
            type_layout.addWidget(BodyLabel("打开类型:"))
            from qfluentwidgets import SpinBox, ComboBox
            self.target_type_combo = ComboBox(self)
            self.target_type_combo.addItems(["网页", "本地程序"])
            current_target_type = self.visual_config.get('boss_key_target_type', 'url')
            self.target_type_combo.setCurrentIndex(1 if current_target_type == 'app' else 0)
            self.target_type_combo.currentIndexChanged.connect(self._on_target_type_changed)
            type_layout.addWidget(self.target_type_combo, 1)
            self.viewLayout.addLayout(type_layout)

            target_layout = QHBoxLayout()
            target_layout.addWidget(BodyLabel("目标内容:"))
            self.target_input = LineEdit(self)
            default_target = self.visual_config.get('boss_key_target', self.visual_config.get('boss_key_url', 'https://www.douyin.com/?recommend=1&from_nav=1'))
            self.target_input.setText(default_target)
            self.target_input.textChanged.connect(self._on_target_changed)
            target_layout.addWidget(self.target_input, 1)
            self.target_browse_btn = PushButton("选择程序", self)
            self.target_browse_btn.clicked.connect(self._browse_boss_key_target)
            target_layout.addWidget(self.target_browse_btn)
            self.viewLayout.addLayout(target_layout)

            delay_layout = QHBoxLayout()
            delay_layout.addWidget(BodyLabel("延迟打开(秒):"))
            self.delay_spin = SpinBox(self)
            self.delay_spin.setRange(0, 60)
            self.delay_spin.setValue(self.visual_config.get('boss_key_delay', 0))
            self.delay_spin.valueChanged.connect(self._on_delay_changed)
            delay_layout.addWidget(self.delay_spin, 1)
            self.viewLayout.addLayout(delay_layout)

            action_layout = QHBoxLayout()
            action_layout.addWidget(BodyLabel("新回合动作:"))
            self.action_combo = ComboBox(self)
            self.action_combo.addItems(["无操作 (仅切回游戏)", "关闭浏览器进程 (强制关闭浏览器)", "暂停/播放(推荐视频网站,使用时关闭音乐软件)"])

            action_val = self.visual_config.get('boss_key_action', 'none')
            if action_val == 'none':
                idx = 0
            elif action_val == 'close':
                idx = 1
            else:
                idx = 2
            self.action_combo.setCurrentIndex(idx)
            self.action_combo.currentIndexChanged.connect(self._on_action_changed)
            action_layout.addWidget(self.action_combo, 1)
            self.viewLayout.addLayout(action_layout)
            self._update_boss_key_target_ui()

        self.widget.setMinimumWidth(400)

    def _browse_flash_image(self):
        extensions = ' '.join('*' + ext for ext in sorted(flash_image_extensions()))
        path, _ = QFileDialog.getOpenFileName(self, "选择图片", "", f"图片文件 ({extensions})")
        if path:
            self.visual_config['flash_path'] = path
            self.path_label.setText(os.path.basename(path))
            self.config_manager.set('visual', self.visual_config)

    def _on_flash_background_changed(self, checked):
        self.visual_config['flash_freeze_background_enabled'] = checked
        self.config_manager.set('visual', self.visual_config)

    def _on_capture_mode_changed(self, index):
        self.visual_config['flash_capture_mode'] = 'compat' if index == 1 else 'low'
        self.config_manager.set('visual', self.visual_config)

    def _browse_flash_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if path:
            self.visual_config['flash_path'] = path
            self.path_label.setText(os.path.basename(path) + " (文件夹)")
            self.config_manager.set('visual', self.visual_config)

    def _browse_death_media(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择媒体文件", "", "媒体文件 (*.png *.jpg *.mp4 *.webm *.avi)")
        if path:
            self.visual_config['death_media_path'] = path
            self.path_label.setText(os.path.basename(path))
            self.config_manager.set('visual', self.visual_config)

    def _on_target_changed(self, text):
        self.visual_config['boss_key_target'] = text
        self.visual_config['boss_key_url'] = text
        self.config_manager.set('visual', self.visual_config)

    def _on_delay_changed(self, value):
        self.visual_config['boss_key_delay'] = value
        self.config_manager.set('visual', self.visual_config)

    def _on_action_changed(self, index):
        if index == 0:
            val = 'none'
        elif index == 1:
            val = 'close'
        else:
            val = 'pause'
        self.visual_config['boss_key_action'] = val
        self.config_manager.set('visual', self.visual_config)

    def _on_target_type_changed(self, index):
        self.visual_config['boss_key_target_type'] = 'app' if index == 1 else 'url'
        self.config_manager.set('visual', self.visual_config)
        self._update_boss_key_target_ui()

    def _browse_boss_key_target(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择本地程序", "", "程序或快捷方式 (*.exe *.lnk *.bat *.cmd)")
        if path:
            self.target_input.setText(path)

    def _update_boss_key_target_ui(self):
        is_app = self.visual_config.get('boss_key_target_type', 'url') == 'app'
        self.target_browse_btn.setVisible(is_app)

        self.action_combo.blockSignals(True)
        self.action_combo.clear()
        if is_app:
            self.action_combo.addItems(["无操作 (仅切回游戏)", "强制关闭目标程序进程", "暂停/播放媒体 (如系统音乐/视频)"])
        else:
            self.action_combo.addItems(["无操作 (仅切回游戏)", "关闭浏览器进程 (强制关闭浏览器)", "暂停/播放(推荐视频网站,使用时关闭音乐软件)"])

        action_val = self.visual_config.get('boss_key_action', 'none')
        if action_val == 'none':
            idx = 0
        elif action_val == 'close':
            idx = 1
        else:
            idx = 2
        self.action_combo.setCurrentIndex(idx)
        self.action_combo.blockSignals(False)
        self.action_combo.setEnabled(True)

    def _on_flash_audio_switch_changed(self, checked):
        self.visual_config['flash_reduce_volume_enabled'] = checked
        self.config_manager.set('visual', self.visual_config)

    def _on_flash_audio_percent_changed(self, value):
        self.flash_audio_value.setText(f"{value}%")
        self.visual_config['flash_reduce_volume_percent'] = value
        self.config_manager.set('visual', self.visual_config)

    def _on_flash_scale_changed(self, index):
        if index == 0:
            val = 'stretch'
        elif index == 1:
            val = 'keep_aspect_crop'
        else:
            val = 'keep_aspect_fit'
        self.visual_config['flash_scale_mode'] = val
        self.config_manager.set('visual', self.visual_config)

    def _on_death_scale_changed(self, index):
        if index == 0:
            val = 'stretch'
        elif index == 1:
            val = 'keep_aspect_crop'
        else:
            val = 'keep_aspect_fit'
        self.visual_config['death_scale_mode'] = val
        self.config_manager.set('visual', self.visual_config)

class KillIconDialog(MessageBoxBase):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.visual_config = self.config_manager.get('visual', {})
        self._setup_ui()

    def _setup_ui(self):
        self.titleLabel = SubtitleLabel('配置击杀图标', self)
        self.viewLayout.addWidget(self.titleLabel)

        self.is_advanced = self.visual_config.get('kill_icon_is_advanced', False)

        # 基础设置
        self.basic_container = QWidget()
        basic_layout = QVBoxLayout(self.basic_container)
        basic_layout.setContentsMargins(0, 0, 0, 0)

        selection_layout = QHBoxLayout()
        selection_layout.addWidget(BodyLabel("全局图标路径:"))
        path_val = self.visual_config.get('kill_icon_path', '')
        self.path_label = BodyLabel(os.path.basename(path_val) if path_val else "未选择")
        self.path_label.setStyleSheet("color: #666666;")
        selection_layout.addWidget(self.path_label, 1)

        self.select_btn = PushButton("选择图片", self)
        self.select_btn.clicked.connect(self._browse_basic_image)
        selection_layout.addWidget(self.select_btn)
        basic_layout.addLayout(selection_layout)

        # 高级设置开关
        adv_switch_layout = QHBoxLayout()
        adv_switch_layout.addWidget(BodyLabel("启用 1-5 杀独立配置:"))
        self.adv_switch = SwitchButton(self)
        self.adv_switch.setOnText("开")
        self.adv_switch.setOffText("关")
        self.adv_switch.setChecked(self.is_advanced)
        self.adv_switch.checkedChanged.connect(self._on_advanced_toggled)
        adv_switch_layout.addStretch()
        adv_switch_layout.addWidget(self.adv_switch)
        basic_layout.addLayout(adv_switch_layout)

        # 图标宽度设置
        width_layout = QHBoxLayout()
        width_layout.addWidget(BodyLabel("图标宽度:"))
        self.width_slider = Slider(Qt.Horizontal, self)
        self.width_slider.setRange(50, 500)
        self.width_slider.setValue(self.visual_config.get('kill_icon_width', 120))

        self.width_value_label = BodyLabel(f"{self.width_slider.value()} px")
        self.width_slider.valueChanged.connect(self._on_width_changed)

        width_layout.addWidget(self.width_slider, 1)
        width_layout.addWidget(self.width_value_label)
        basic_layout.addLayout(width_layout)

        # 图标高度设置
        height_layout = QHBoxLayout()
        height_layout.addWidget(BodyLabel("图标高度:"))
        self.height_slider = Slider(Qt.Horizontal, self)
        self.height_slider.setRange(50, 500)
        self.height_slider.setValue(self.visual_config.get('kill_icon_height', 120))

        self.height_value_label = BodyLabel(f"{self.height_slider.value()} px")
        self.height_slider.valueChanged.connect(self._on_height_changed)

        height_layout.addWidget(self.height_slider, 1)
        height_layout.addWidget(self.height_value_label)
        basic_layout.addLayout(height_layout)

        # 图标底部偏移设置
        bottom_layout = QHBoxLayout()
        bottom_layout.addWidget(BodyLabel("距离底部位置:"))
        self.bottom_slider = Slider(Qt.Horizontal, self)
        self.bottom_slider.setRange(0, 500)
        self.bottom_slider.setValue(self.visual_config.get('kill_icon_bottom', 100))

        self.bottom_value_label = BodyLabel(f"{self.bottom_slider.value()} px")
        self.bottom_slider.valueChanged.connect(self._on_bottom_changed)

        bottom_layout.addWidget(self.bottom_slider, 1)
        bottom_layout.addWidget(self.bottom_value_label)
        basic_layout.addLayout(bottom_layout)

        self.viewLayout.addWidget(self.basic_container)

        # 高级设置容器
        self.adv_container = QWidget()
        adv_layout = QVBoxLayout(self.adv_container)
        adv_layout.setContentsMargins(0, 0, 0, 0)

        self.adv_labels = []
        icons_config = self.visual_config.get('kill_icons_1_5', [{} for _ in range(5)])

        for i in range(5):
            h_layout = QHBoxLayout()
            h_layout.addWidget(BodyLabel(f"{i+1} 杀图标:"))
            path_val = icons_config[i].get('path', '')
            label = BodyLabel(os.path.basename(path_val) if path_val else "未选择")
            label.setStyleSheet("color: #666666;")
            self.adv_labels.append(label)
            h_layout.addWidget(label, 1)

            btn = PushButton("选择图片", self)
            btn.clicked.connect(lambda checked=False, idx=i: self._browse_adv_image(idx))
            h_layout.addWidget(btn)
            adv_layout.addLayout(h_layout)

        self.viewLayout.addWidget(self.adv_container)

        self._update_visibility()
        self.widget.setMinimumWidth(450)

    def _update_visibility(self):
        if self.is_advanced:
            self.adv_container.show()
            self.basic_container.findChild(QHBoxLayout).setEnabled(False) # 只是视觉上，实际用 hide 更简单但影响布局
        else:
            self.adv_container.hide()

    def _on_advanced_toggled(self, checked):
        self.is_advanced = checked
        self.visual_config['kill_icon_is_advanced'] = checked
        self._update_visibility()
        self.config_manager.set('visual', self.visual_config)

    def _on_width_changed(self, value):
        self.width_value_label.setText(f"{value} px")
        self.visual_config['kill_icon_width'] = value
        self.config_manager.set('visual', self.visual_config)

    def _on_height_changed(self, value):
        self.height_value_label.setText(f"{value} px")
        self.visual_config['kill_icon_height'] = value
        self.config_manager.set('visual', self.visual_config)

    def _on_bottom_changed(self, value):
        self.bottom_value_label.setText(f"{value} px")
        self.visual_config['kill_icon_bottom'] = value
        self.config_manager.set('visual', self.visual_config)

    def _browse_basic_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择击杀图标", "", "媒体文件 (*.png *.jpg *.jpeg *.bmp *.gif *.mp4 *.webm)")
        if path:
            self.visual_config['kill_icon_path'] = path
            self.path_label.setText(os.path.basename(path))
            self.config_manager.set('visual', self.visual_config)

    def _browse_adv_image(self, index):
        path, _ = QFileDialog.getOpenFileName(self, f"选择 {index+1} 杀图标", "", "媒体文件 (*.png *.jpg *.jpeg *.bmp *.gif *.mp4 *.webm)")
        if path:
            icons = self.visual_config.get('kill_icons_1_5', [{} for _ in range(5)])
            icons[index]['path'] = path
            self.visual_config['kill_icons_1_5'] = icons
            self.adv_labels[index].setText(os.path.basename(path))
            self.config_manager.set('visual', self.visual_config)

class VisualPage(ScrollArea):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent=parent)
        self.config_manager = config_manager
        self.parent_window = parent

        self.view = QWidget(self)
        self.view.setObjectName("view")
        self.layout = QVBoxLayout(self.view)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(15)
        self.layout.setAlignment(Qt.AlignTop)

        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setObjectName("visualPage")
        self.setStyleSheet("QScrollArea {background: transparent; border: none;} #view {background: transparent;}")

        self._init_config()
        self._setup_ui()

    def _init_config(self):
        self.visual_config = self.config_manager.get('visual', {
            'visual_master_enabled': True,
            'flash_enabled': False,
            'flash_path': '',
            'flash_scale_mode': 'stretch',
            'flash_freeze_background_enabled': True,
            'flash_reduce_volume_enabled': False,
            'flash_reduce_volume_percent': 25,
            'death_media_enabled': False,
            'death_media_path': '',
            'death_scale_mode': 'stretch',
            'boss_key_enabled': False,
            'boss_key_target_type': 'url',
            'boss_key_target': 'https://www.douyin.com/?recommend=1&from_nav=1',
            'boss_key_url': 'https://www.douyin.com/?recommend=1&from_nav=1',
            'boss_key_delay': 0,
            'boss_key_action': 'none',
            'kill_icon_enabled': False,
            'kill_icon_is_advanced': False,
            'kill_icon_width': 120,
            'kill_icon_height': 120,
            'kill_icon_bottom': 100,
            'kill_icon_path': '',
            'kill_icons_1_5': [{} for _ in range(5)]
        })

    def _update_ui_from_config(self):
        self._init_config()
        self.flash_card.switch_btn.blockSignals(True)
        self.flash_card.switch_btn.setChecked(self.visual_config.get('flash_enabled', False))
        self.flash_card.switch_btn.blockSignals(False)

        self.kill_icon_card.switch_btn.blockSignals(True)
        self.kill_icon_card.switch_btn.setChecked(self.visual_config.get('kill_icon_enabled', False))
        self.kill_icon_card.switch_btn.blockSignals(False)

        self.death_card.switch_btn.blockSignals(True)
        self.death_card.switch_btn.setChecked(self.visual_config.get('death_media_enabled', False))
        self.death_card.switch_btn.blockSignals(False)

        self.boss_key_card.switch_btn.blockSignals(True)
        self.boss_key_card.switch_btn.setChecked(self.visual_config.get('boss_key_enabled', False))
        self.boss_key_card.switch_btn.blockSignals(False)

        self.fullscreen_support_card.switchButton.blockSignals(True)
        self.fullscreen_support_card.switchButton.setChecked(self.config_manager.get('launch_use_vulkan', False))
        self.fullscreen_support_card.switchButton.blockSignals(False)

    def _save_config(self):
        self.config_manager.set('visual', self.visual_config)

    def _setup_ui(self):
        self.practical_group = SettingCardGroup("实用功能", self.view)

        # 1. 闪光弹设置
        self.flash_card = VisualSettingCard(
            icon=FIF.PHOTO,
            title="自定义闪光效果",
            content="当被闪白时，在屏幕上叠加自定义图片",
            parent=self.practical_group
        )
        self.flash_card.switch_btn.setChecked(self.visual_config.get('flash_enabled', False))
        self.flash_card.switch_btn.checkedChanged.connect(self._on_flash_switch_changed)
        self.flash_card.setting_btn.clicked.connect(lambda: self._open_settings('flash'))
        self.practical_group.addSettingCard(self.flash_card)

        self.flash_test_card = PushSettingCard(
            text="预览闪光（2.5 秒）", icon=FIF.PHOTO,
            title="测试图片显示", content="先在桌面预览，再进入游戏检查闪光联动",
            parent=self.practical_group)
        self.flash_test_card.clicked.connect(self._preview_flash)
        self.practical_group.addSettingCard(self.flash_test_card)


        self.fullscreen_support_card = SwitchSettingCard(
            icon=FIF.GAME,
            title="全屏支持（Vulkan）",
            content="通过本工具启动 CS2 时自动附加 -vulkan，提升全屏模式下视觉覆盖兼容性",
            parent=self.practical_group
        )
        self.fullscreen_support_card.switchButton.setChecked(self.config_manager.get('launch_use_vulkan', False))
        self.fullscreen_support_card.switchButton.checkedChanged.connect(self._on_fullscreen_support_changed)
        self.practical_group.addSettingCard(self.fullscreen_support_card)

        # 2. 击杀图标设置
        self.kill_icon_card = VisualSettingCard(
            icon=FIF.GAME,
            title="击杀图标显示",
            content="在击杀敌人时，在屏幕下方显示击杀图标（支持1-5杀高阶配置）",
            parent=self.practical_group
        )
        self.kill_icon_card.switch_btn.setChecked(self.visual_config.get('kill_icon_enabled', False))
        self.kill_icon_card.switch_btn.checkedChanged.connect(self._on_kill_icon_switch_changed)
        self.kill_icon_card.setting_btn.clicked.connect(lambda: self._open_settings('kill_icon'))
        self.practical_group.addSettingCard(self.kill_icon_card)

        self.layout.addWidget(self.practical_group)

        self.flash_status_label = BodyLabel(self.view)
        self.flash_status_label.setWordWrap(True)
        self.layout.addWidget(self.flash_status_label)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._refresh_flash_status)
        self.status_timer.start(500)
        self._refresh_flash_status()

        self.entertainment_group = SettingCardGroup("娱乐功能", self.view)

        # 3. 死亡画面设置
        self.death_card = VisualSettingCard(
            icon=FIF.VIDEO,
            title="自定义死亡画面",
            content="死亡时在屏幕上播放指定的图片或视频",
            parent=self.entertainment_group
        )
        self.death_card.switch_btn.setChecked(self.visual_config.get('death_media_enabled', False))
        self.death_card.switch_btn.checkedChanged.connect(self._on_death_switch_changed)
        self.death_card.setting_btn.clicked.connect(lambda: self._open_settings('death'))
        self.entertainment_group.addSettingCard(self.death_card)

        # 4. 死亡一键切屏 (老板键)
        self.boss_key_card = VisualSettingCard(
            icon=FIF.HIDE,
            title="死亡一键切屏",
            content="死亡后自动最小化游戏并打开指定网页，新回合开始时自动切回",
            parent=self.entertainment_group
        )
        self.boss_key_card.switch_btn.setChecked(self.visual_config.get('boss_key_enabled', False))
        self.boss_key_card.switch_btn.checkedChanged.connect(self._on_boss_key_switch_changed)
        self.boss_key_card.setting_btn.clicked.connect(lambda: self._open_settings('boss_key'))
        self.entertainment_group.addSettingCard(self.boss_key_card)

        self.layout.addWidget(self.entertainment_group)

    def _open_settings(self, type):
        if type == 'kill_icon':
            dialog = KillIconDialog(self.config_manager, self)
        else:
            dialog = VisualConfigDialog(self.config_manager, type, self)
        dialog.exec()

    def _refresh_flash_status(self):
        handler = getattr(self.parent_window, 'visual_handler', None)
        if handler:
            self.flash_status_label.setText(handler.flash_status())

    def _preview_flash(self):
        handler = getattr(self.parent_window, 'visual_handler', None)
        if not handler:
            return
        handler._update_config(force=True)
        if not handler.overlay.preview_flash():
            InfoBar.warning("无法预览", "请先在闪光设置中选择可读取的图片或图片文件夹。", parent=self)

    def _on_flash_switch_changed(self, is_checked):
        self.visual_config['flash_enabled'] = is_checked
        self._save_config()
        if hasattr(self.parent_window, 'update_home_status'):
            self.parent_window.update_home_status()

    def _on_kill_icon_switch_changed(self, is_checked):
        self.visual_config['kill_icon_enabled'] = is_checked
        self._save_config()
        if hasattr(self.parent_window, 'update_home_status'):
            self.parent_window.update_home_status()

    def _on_death_switch_changed(self, is_checked):
        self.visual_config['death_media_enabled'] = is_checked
        self._save_config()
        if hasattr(self.parent_window, 'update_home_status'):
            self.parent_window.update_home_status()

    def _on_boss_key_switch_changed(self, is_checked):
        self.visual_config['boss_key_enabled'] = is_checked
        self._save_config()
        if hasattr(self.parent_window, 'update_home_status'):
            self.parent_window.update_home_status()

    def _on_fullscreen_support_changed(self, is_checked):
        self.config_manager.set('launch_use_vulkan', is_checked)

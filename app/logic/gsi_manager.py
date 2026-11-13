import os
import json
import threading
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from .gsi_sound_handler import GSISoundHandler

class ReusableHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

class GSIHandler(BaseHTTPRequestHandler):
    # 处理GSI POST请求
    disable_nagle_algorithm = True

    def address_string(self):
        # 禁用耗时的反向 DNS 解析，直接返回 IP
        return self.client_address[0]

    # 隐藏成功的POST请求日志，以保持控制台清洁
    def log_message(self, format, *args):
        if self.command == "POST" and "200" in args:
            return
        super().log_message(format, *args)

    def do_POST(self):
        try:
            content_length = int(self.headers['Content-Length'])
            post_data_bytes = self.rfile.read(content_length)

            # 使用回调函数处理数据
            if hasattr(self.server, 'data_callback'):
                self.server.data_callback(post_data_bytes)

            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"OK")
        except Exception as e:
            print(f"处理GSI POST请求时出错: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Error processing GSI data")


class GSIManager:
    # 管理 Game State Integration (GSI) 的所有功能
    def __init__(self, cs2_path=None, config_manager=None):
        self.cs2_path = cs2_path
        self.config_manager = config_manager
        self._server_thread = None
        self._httpd = None
        self._is_starting = False
        self.data_callback = None
        self.current_port = 3000  # 当前使用的端口
        self.current_host = '127.0.0.1'  # 当前使用的主机

        if self.config_manager:
            saved_port = self.config_manager.get("gsi_port", 3000)
            if isinstance(saved_port, int) and 1 <= saved_port <= 65535:
                self.current_port = saved_port

        # 初始化GSI音效处理器
        self.sound_handler = GSISoundHandler(config_manager) if config_manager else None

        # 设置默认的数据处理回调
        if self.sound_handler:
            self.register_data_callback(self._default_data_handler)

    def get_gsi_cfg_content(self):
        # 生成GSI配置文件内容，使用当前端口
        return f'''
"gsi-cs2"
{{
    "uri" "http://{self.current_host}:{self.current_port}"
    "timeout" "1.0"
    "buffer"  "0.0"
    "throttle" "0.0"
    "heartbeat" "1.0"
    "auth"
    {{
        "token" "TOKEN"
    }}
    "output"
    {{
        "precision_time" "3"
        "precision_position" "1"
        "precision_vector" "3"
    }}
    "data"
    {{
        "map_round_wins" "0"
        "map" "0"
        "player_id" "1"
        "player_match_stats" "1"
        "player_state" "1"
        "player_weapons" "1"
        "provider" "1"
        "round" "1"
        "allgrenades" "0"
        "allplayers_id" "0"
        "allplayers_match_stats" "0"
        "allplayers_position" "0"
        "allplayers_state" "0"
        "allplayers_weapons" "0"
        "bomb" "1"
        "phase_countdowns" "1"
        "player_position" "0"
    }}
}}
'''

    def set_cs2_path(self, path):
        self.cs2_path = path

    def create_gsi_cfg(self):
        if not self.cs2_path or not os.path.isdir(self.cs2_path):
            return False, "CS2路径未设置或无效。"
        cfg_dir = os.path.join(self.cs2_path, "game", "csgo", "cfg")
        if not os.path.isdir(cfg_dir):
            return False, f"找不到CS2的CFG目录: {cfg_dir}"
        cfg_path = os.path.join(cfg_dir, "gamestate_integration_cs2toolkit.cfg")
        try:
            with open(cfg_path, 'w', encoding='utf-8') as f:
                f.write(self.get_gsi_cfg_content())
            return True, f"GSI配置文件已成功创建于: {cfg_path}\n使用端口: {self.current_port}"
        except IOError as e:
            return False, f"无法写入GSI配置文件: {e}"

    def register_data_callback(self, callback):
        # 注册处理GSI数据的回调函数
        self.data_callback = callback

    def _default_data_handler(self, post_data_bytes):
        # 默认的GSI数据处理器
        if self.sound_handler:
            try:
                # 处理GSI数据并播放相应音效
                self.sound_handler.handle_gsi_data(post_data_bytes)
            except Exception as e:
                print(f"处理GSI数据时出错: {e}")

    def set_gsi_sound_pack(self, pack_path: str):
        # 设置GSI音效包
        if self.sound_handler:
            return self.sound_handler.set_gsi_sound_pack(pack_path)
        return False

    def set_gsi_sound_volume(self, volume: float):
        # 设置GSI音效音量
        if self.sound_handler:
            self.sound_handler.set_volume(volume)

    def test_gsi_sound(self, sound_type: str):
        # 测试GSI音效
        if self.sound_handler:
            self.sound_handler.test_sound(sound_type)

    def _check_port_available(self, host, port):
        # 检查端口是否可用
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                result = sock.connect_ex((host, port))
                return result != 0
        except Exception:
            return False

    def _find_available_port(self, host='127.0.0.1', start_port=3000, max_attempts=10):
        # 查找可用端口
        for port in range(start_port, start_port + max_attempts):
            if self._check_port_available(host, port):
                return port
        return None

    def _server_target(self, host='127.0.0.1', port=3000):
        # 检查指定端口是否可用，如果不可用则自动寻找其他端口
        if not self._check_port_available(host, port):
            print(f"端口 {port} 已被占用，正在寻找其他可用端口...")
            available_port = self._find_available_port(host, port)
            if available_port is None:
                self._is_starting = False
                error_msg = f"无法找到可用端口（尝试范围：{port}-{port+9}），请检查是否有其他程序（如其他辅助软件）占用了大量端口。"
                print(error_msg)
                if self.data_callback:
                    error_info = {"error": "server_start_failed", "message": error_msg}
                    self.data_callback(json.dumps(error_info).encode('utf-8'))
                return

            # 如果自动更换了端口，通知UI提示用户需要重新生成CFG
            if self.data_callback:
                warning_info = {"warning": "port_changed", "old_port": port, "new_port": available_port}
                self.data_callback(json.dumps(warning_info).encode('utf-8'))

            port = available_port
            print(f"找到可用端口：{port}")

        # 更新当前使用的端口和主机
        self.current_port = port
        self.current_host = host

        try:
            server_address = (host, port)
            self._httpd = ReusableHTTPServer(server_address, GSIHandler)
            self.current_port = self._httpd.server_port
            port = self.current_port
            # 将回调函数传递给服务器实例，以便GSIHandler可以访问它
            self._httpd.data_callback = self.data_callback
            self._is_starting = False
            print(f"GSI服务器已在 {host}:{port} 上启动...")

            # 通知UI服务器启动成功
            if self.data_callback:
                success_info = {"success": "server_started", "port": port, "host": host}
                self.data_callback(json.dumps(success_info).encode('utf-8'))

            self._httpd.serve_forever()
        except OSError as e:
            self._is_starting = False
            print(f"无法启动GSI服务器: {e}")
            if self.data_callback:
                # 通过回调通知UI错误
                error_info = {"error": "server_start_failed", "message": str(e)}
                self.data_callback(json.dumps(error_info).encode('utf-8'))
        except Exception as e:
            self._is_starting = False
            print(f"GSI服务器发生意外错误: {e}")
            if self.data_callback:
                error_info = {"error": "server_unexpected_error", "message": str(e)}
                self.data_callback(json.dumps(error_info).encode('utf-8'))
        finally:
            if self._httpd is None:
                self._is_starting = False

    def start_server(self):
        if self._is_starting or (self._server_thread and self._server_thread.is_alive()):
            print("GSI服务器已在运行中。")
            return False

        self._is_starting = True
        self._server_thread = threading.Thread(
            target=self._server_target,
            args=(self.current_host, self.current_port), daemon=True)
        self._server_thread.start()
        return True

    def stop_server(self):
        self._is_starting = False
        if self._httpd:
            print("正在关闭GSI服务器...")
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=1)
        self._server_thread = None
        print("GSI服务器已停止。")

    def is_running(self):
        return self._httpd is not None and self._server_thread is not None and self._server_thread.is_alive()

    def is_starting(self):
        return self._is_starting

# build.py 构建脚本
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile


def remove_readonly(func, path, excinfo):
    os.chmod(path, stat.S_IWRITE)
    func(path)

def get_version():
    main_window_path = os.path.join(os.path.dirname(__file__), 'app', 'main_window.py')
    try:
        with open(main_window_path, 'r', encoding='utf-8') as f:
            content = f.read()
            match = re.search(r'self\.version\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                return match.group(1)
    except Exception as e:
        print(f"提取版本号失败: {e}")
    return "unknown"


def find_standalone_output_dir(dist_root, output_filename, entry_file):
    entry_stem = os.path.splitext(os.path.basename(entry_file))[0]
    candidates = [
        os.path.join(dist_root, f"{output_filename}.dist"),
        os.path.join(dist_root, f"{entry_stem}.dist"),
    ]

    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate

    if os.path.isdir(dist_root):
        for name in os.listdir(dist_root):
            candidate = os.path.join(dist_root, name)
            if not os.path.isdir(candidate) or not name.endswith(".dist"):
                continue
            exe_candidate = os.path.join(candidate, f"{output_filename}.exe")
            if os.path.isfile(exe_candidate):
                return candidate

    raise FileNotFoundError(f"找不到 Nuitka 输出目录: {os.path.join(dist_root, f'{output_filename}.dist')}")


def load_release_endpoints(project_root):
    local_config_path = os.path.join(project_root, "release_endpoints.local.json")
    update_url = os.environ.get("CS2TOOLKIT_UPDATE_URL", "").strip()
    announcement_url = os.environ.get("CS2TOOLKIT_ANNOUNCEMENT_URL", "").strip()

    if os.path.isfile(local_config_path):
        with open(local_config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        update_url = str(data.get("update_url", update_url)).strip()
        announcement_url = str(data.get("announcement_url", announcement_url)).strip()

    return update_url, announcement_url


def inject_release_endpoints(project_root, update_url, announcement_url):
    file_path = os.path.join(project_root, "app", "release_endpoints.py")
    with open(file_path, "r", encoding="utf-8") as f:
        original_content = f.read()

    if update_url and announcement_url:
        replaced_content = re.sub(
            r'^UPDATE_URL\s*=\s*".*"$',
            f'UPDATE_URL = "{update_url}"',
            original_content,
            flags=re.MULTILINE
        )
        replaced_content = re.sub(
            r'^ANNOUNCEMENT_URL\s*=\s*".*"$',
            f'ANNOUNCEMENT_URL = "{announcement_url}"',
            replaced_content,
            flags=re.MULTILINE
        )
        with open(file_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(replaced_content)

    return file_path, original_content


def create_release_zip(package_root, output_filename):
    archive_path = os.path.join(os.path.dirname(package_root), f"{output_filename}_portable.zip")
    if os.path.exists(archive_path):
        os.remove(archive_path)

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
        for root, _, files in os.walk(package_root):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                arcname = os.path.relpath(file_path, os.path.dirname(package_root))
                zipf.write(file_path, arcname)

    return archive_path

def build_exe():
    project_root = os.path.dirname(os.path.abspath(__file__))
    entry_file = "main.py"
    version = get_version()
    output_filename = f"CS2Toolkit_v{version}"
    sponsorship_path = os.path.join(project_root, "sponsorship.jpg")
    update_url, announcement_url = load_release_endpoints(project_root)
    print(f"准备编译版本: {output_filename}")
    if update_url and announcement_url:
        print("已注入本地更新与公告链接")
    else:
        print("未提供本地更新与公告链接，使用仓库占位符")

    endpoints_file_path, original_endpoints_content = inject_release_endpoints(
        project_root, update_url, announcement_url
    )

    try:
        cmd = [
            sys.executable, "-m", "nuitka",
            "--standalone",
            "--windows-console-mode=disable",
            "--enable-plugin=pyside6",
            "--include-package=PySide6.QtMultimedia",
            "--include-qt-plugins=multimedia",
            "--include-qt-plugins=platforms",
            "--include-module=pycaw.pycaw",
            "--include-package=dxcam",
            "--include-package=numpy",
            "--assume-yes-for-downloads",
            "--output-dir=dist",
            f"--output-filename={output_filename}",
            "--noinclude-qt-translations",
            "--windows-icon-from-ico=app_icon.ico",
            "--mingw64",
            "--nofollow-import-to=cv2",
            "--nofollow-import-to=tkinter",
            "--nofollow-import-to=idlelib",
            "--nofollow-import-to=pkg_resources",
            "--nofollow-import-to=PySide6.QtPdf",
            "--nofollow-import-to=PySide6.QtPdfWidgets",
            "--remove-output",
            entry_file
        ]

        if os.path.isfile(sponsorship_path):
            cmd.insert(cmd.index("--mingw64"), "--include-data-files=sponsorship.jpg=sponsorship.jpg")

        subprocess.run(cmd, check=True, cwd=project_root)

        dist_root = os.path.join(project_root, "dist")
        build_output_dir = find_standalone_output_dir(dist_root, output_filename, entry_file)
        package_root = os.path.join(dist_root, output_filename)
        runtime_dir = os.path.join(package_root, "runtime")
        launcher_path = os.path.join(package_root, f"{output_filename}.exe")
        exe_name = f"{output_filename}.exe"
        runtime_exe_name = f"{output_filename}_runtime.exe"

        if os.path.exists(package_root):
            try:
                shutil.rmtree(package_root, onerror=remove_readonly)
            except PermissionError as e:
                print(f"无法删除旧的构建目录，可能是程序正在运行。请关闭程序后再试。\n错误信息: {e}")
                sys.exit(1)

        os.makedirs(package_root, exist_ok=True)
        shutil.move(build_output_dir, runtime_dir)

        runtime_exe_path = os.path.join(runtime_dir, exe_name)
        renamed_runtime_exe_path = os.path.join(runtime_dir, runtime_exe_name)
        if not os.path.isfile(runtime_exe_path):
            raise FileNotFoundError(f"找不到主程序: {runtime_exe_path}")
        os.replace(runtime_exe_path, renamed_runtime_exe_path)

        launcher_source = os.path.join(package_root, "_launcher.py")
        launcher_content = f'''import ctypes
import os
import subprocess
import sys


def main():
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    runtime_dir = os.path.join(base_dir, "runtime")
    target = os.path.join(runtime_dir, "{runtime_exe_name}")

    if not os.path.isfile(target):
        ctypes.windll.user32.MessageBoxW(
            None,
            f"缺少运行文件:\\n{{target}}",
            "CS2Toolkit",
            0x10,
        )
        return

    creationflags = 0
    creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    try:
        subprocess.Popen([target], cwd=runtime_dir, close_fds=True, creationflags=creationflags)
    except Exception as e:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"启动失败:\\n{{e}}",
            "CS2Toolkit",
            0x10,
        )


if __name__ == "__main__":
    main()
'''

        with open(launcher_source, "w", encoding="utf-8", newline="\n") as f:
            f.write(launcher_content)

        launcher_cmd = [
            sys.executable, "-m", "nuitka",
            "--onefile",
            "--windows-console-mode=disable",
            "--assume-yes-for-downloads",
            f"--output-dir={package_root}",
            f"--output-filename={output_filename}",
            "--windows-icon-from-ico=app_icon.ico",
            "--mingw64",
            "--nofollow-import-to=tkinter",
            "--remove-output",
            launcher_source
        ]
        subprocess.run(launcher_cmd, check=True, cwd=project_root)
        os.remove(launcher_source)

        archive_path = create_release_zip(package_root, output_filename)

        print(f"已整理输出目录: {package_root}")
        print(f"已生成发布压缩包: {archive_path}")
        print(f"运行请双击: {launcher_path}")
    finally:
        with open(endpoints_file_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(original_endpoints_content)

if __name__ == "__main__":
    build_exe()
    print("构建完成")

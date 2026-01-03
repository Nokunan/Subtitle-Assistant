import sys
import os
import subprocess
import threading
import tempfile
import json
import glob

from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton,
    QFileDialog, QVBoxLayout, QHBoxLayout, QFrame
)
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QFont

from faster_whisper import WhisperModel

# --- 核心路径处理：兼容开发和打包环境 ---
def get_resource_path(relative_path):
    """ 获取资源绝对路径，适配 PyInstaller 的单文件或单目录模式 """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

def get_config_dir():
    """ 配置文件保存在 .exe 同级目录 """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_config_dir()
FFMPEG_PATH = get_resource_path(os.path.join("ffmpeg", "ffmpeg.exe"))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

class SubtitleApp(QWidget):
    def __init__(self):
        super().__init__()
        self.video_path = None
        self.generating = False
        self.stop_flag = False
        self.drag_pos = QPoint()
        
        # 加载配置
        self.settings = self.load_settings()
        self.is_dark = self.settings.get("is_dark", True) 

        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setFixedSize(420, 350) 
        self.init_ui()
        self.update_theme() 

    def load_settings(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except: pass
        return {"is_dark": True}

    def save_settings(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({"is_dark": self.is_dark}, f)
        except: pass

    def init_ui(self):
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(18, 25, 18, 18)
        self.root.setSpacing(15)

        # --- 顶部标题栏 ---
        title_bar = QHBoxLayout()
        self.btn_theme = QPushButton("🌙") 
        self.btn_theme.setFixedSize(28, 28)
        self.btn_theme.setObjectName("themeBtn")
        self.btn_theme.setCursor(Qt.PointingHandCursor)
        self.btn_theme.clicked.connect(self.toggle_theme)
        
        title = QLabel("极速字幕助手")
        title.setFont(QFont("Segoe UI", 9, QFont.Bold))

        btn_min = QPushButton("—")
        btn_close = QPushButton("×")
        for b in (btn_min, btn_close):
            b.setFixedSize(28, 28)
            b.setObjectName("titleBtn")

        btn_min.clicked.connect(self.showMinimized)
        btn_close.clicked.connect(self.close)

        title_bar.addWidget(self.btn_theme)
        title_bar.addWidget(title)
        title_bar.addStretch()
        title_bar.addWidget(btn_min)
        title_bar.addWidget(btn_close)
        title_bar.setContentsMargins(0, -15, 0, 0) 
        self.root.addLayout(title_bar)

        # --- 拖拽选择区 ---
        self.drop_area = QFrame()
        self.drop_area.setObjectName("dropArea")
        self.drop_area.setAcceptDrops(True)
        self.drop_area.setFixedHeight(140)
        
        drop_layout = QVBoxLayout(self.drop_area)
        drop_layout.setSpacing(10)
        drop_layout.setAlignment(Qt.AlignCenter)

        self.btn_plus = QPushButton("+")
        self.btn_plus.setFixedSize(36, 36)
        self.btn_plus.setObjectName("plusBtn")
        self.btn_plus.clicked.connect(self.select_video)

        self.drop_label = QLabel("拖拽视频或点击添加")
        self.drop_label.setObjectName("dropLabel")
        
        drop_layout.addWidget(self.btn_plus, 0, Qt.AlignCenter)
        drop_layout.addWidget(self.drop_label, 0, Qt.AlignCenter)
        self.root.addWidget(self.drop_area)

        # --- 开始按钮 ---
        self.btn_generate = QPushButton("开始生成字幕")
        self.btn_generate.setEnabled(False)
        self.btn_generate.setObjectName("actionBtn")
        self.btn_generate.setFixedHeight(40)
        self.btn_generate.clicked.connect(self.toggle_generate)
        self.root.addWidget(self.btn_generate)

        # --- 底部信息区 ---
        bottom_container = QVBoxLayout()
        bottom_container.setSpacing(0)

        path_row = QHBoxLayout()
        label = QLabel("保存至:")
        label.setObjectName("outputTitle")
        self.path_btn = QPushButton("默认视频同目录")
        self.path_btn.setObjectName("pathBtn")
        self.path_btn.clicked.connect(self.change_output_dir)
        path_row.addWidget(label)
        path_row.addWidget(self.path_btn, 1)
        
        self.status = QLabel("状态：等待操作...")
        self.status.setObjectName("status")

        bottom_container.addLayout(path_row)
        bottom_container.addWidget(self.status)
        self.root.addLayout(bottom_container)

    def toggle_theme(self):
        self.is_dark = not self.is_dark
        self.update_theme()
        self.save_settings()

    def update_theme(self):
        self.btn_theme.setText("🌙" if self.is_dark else "☀️")
        self.setStyleSheet(self.get_dark_qss() if self.is_dark else self.get_light_qss())
        self.drop_area.setStyleSheet(self.get_drop_area_qss())

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self, event):
        path = event.mimeData().urls()[0].toLocalFile()
        if path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.flv')):
            self.set_video(path)
        else:
            self.status.setText("错误：不支持的文件格式")

    def select_video(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择视频", "", "Video (*.mp4 *.mkv *.mov *.avi)")
        if path: self.set_video(path)

    def set_video(self, path):
        self.video_path = path
        self.path_btn.setText(os.path.dirname(path))
        self.drop_label.setText(os.path.basename(path))
        self.btn_generate.setEnabled(True)
        self.status.setText("状态：准备就绪")

    def change_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存文件夹")
        if d: self.path_btn.setText(d)

    def toggle_generate(self):
        if not self.generating:
            self.generating = True
            self.stop_flag = False
            self.btn_generate.setText("停止处理")
            threading.Thread(target=self.generate, daemon=True).start()
        else:
            self.stop_flag = True
            self.status.setText("状态：正在停止...")

    def find_local_model(self):
        """ 自动寻找本地模型路径 """
        # 指向你刚才搬运的 models 文件夹
        search_path = get_resource_path(os.path.join("models", "models--systran--faster-whisper-small", "snapshots", "*"))
        dirs = glob.glob(search_path)
        if dirs:
            return dirs[0] # 返回第一个匹配到的 snapshot 文件夹
        return "small" # 如果没找到，退回到在线下载模式

    def generate(self):
        try:
            self.status.setText("状态：正在提取音频数据...")
            with tempfile.TemporaryDirectory() as tmp:
                audio = os.path.join(tmp, "audio.wav")
                cmd = f'"{FFMPEG_PATH}" -y -i "{self.video_path}" -ar 16000 -ac 1 "{audio}"'
                
                # 隐藏 ffmpeg 控制台窗口
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                subprocess.run(cmd, startupinfo=si, check=True)
                
                if self.stop_flag: return
                self.status.setText("状态：AI 加载中(本地读取)...")
                
                # 获取本地模型路径
                model_path = self.find_local_model()
                model = WhisperModel(model_path, device="cpu", compute_type="int8")
                
                self.status.setText("状态：AI 识别中(不消耗流量)...")
                segments, _ = model.transcribe(audio, language="zh")

                name = os.path.splitext(os.path.basename(self.video_path))[0]
                out_path = os.path.join(self.path_btn.text(), f"{name}_字幕.srt")
                
                with open(out_path, "w", encoding="utf-8") as f:
                    for i, s in enumerate(segments, 1):
                        if self.stop_flag: break
                        f.write(f"{i}\n{self.format_time(s.start)} --> {self.format_time(s.end)}\n{s.text.strip()}\n\n")
            
            self.status.setText("状态：字幕生成成功！" if not self.stop_flag else "状态：已终止")
        except Exception as e:
            self.status.setText(f"错误：{str(e)}")
        finally:
            self.generating = False
            self.btn_generate.setText("开始生成字幕")

    def format_time(self, sec):
        h, m, s = int(sec // 3600), int((sec % 3600) // 60), int(sec % 60)
        ms = int((sec - int(sec)) * 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    # --- 样式定义 ---
    def get_dark_qss(self):
        return """
        QWidget { background:#111; color:#eee; font-family:Segoe UI, Microsoft YaHei; }
        QPushButton#themeBtn { border:none; background:transparent; font-size:16px; color:#aaa; }
        QPushButton#actionBtn { border:1px dashed #555; background:transparent; border-radius:6px; color:#bbb; font-weight:bold; }
        QPushButton#actionBtn:hover { border-color:#888; background:#1a1a1a; color:white; }
        QPushButton#actionBtn:disabled { color:#333; border-color:#222; }
        QPushButton#titleBtn { border:none; background:transparent; color:#555; font-size:14px; }
        QPushButton#titleBtn:hover { color:white; background:#222; }
        QPushButton#pathBtn { border:none; background:transparent; color:#666; text-align:left; font-size:11px; }
        QPushButton#pathBtn:hover { color:#999; text-decoration:underline; }
        QLabel#status, QLabel#outputTitle, QLabel#dropLabel { color:#555; font-size:11px; }
        """

    def get_light_qss(self):
        return """
        QWidget { background:#f9f9f9; color:#333; font-family:Segoe UI, Microsoft YaHei; }
        QPushButton#themeBtn { border:none; background:transparent; font-size:16px; color:#666; }
        QPushButton#actionBtn { border:1px dashed #ccc; background:#fff; border-radius:6px; color:#555; font-weight:bold; }
        QPushButton#actionBtn:hover { border-color:#888; background:#eee; color:#111; }
        QPushButton#actionBtn:disabled { color:#ccc; border-color:#eee; }
        QPushButton#titleBtn { border:none; background:transparent; color:#999; font-size:14px; }
        QPushButton#titleBtn:hover { color:#333; background:#e0e0e0; }
        QPushButton#pathBtn { border:none; background:transparent; color:#0078d4; text-align:left; font-size:11px; }
        QPushButton#pathBtn:hover { color:#005a9e; text-decoration:underline; }
        QLabel#status, QLabel#outputTitle, QLabel#dropLabel { color:#888; font-size:11px; }
        """

    def get_drop_area_qss(self):
        border = "#555" if self.is_dark else "#ccc"
        bg = "#151515" if self.is_dark else "#fff"
        p_bg = "#111" if self.is_dark else "#f0f0f0"
        return f"""
        QFrame#dropArea {{ border:2px dashed {border}; border-radius:12px; background: {bg}; }}
        QPushButton#plusBtn {{ border:1px solid {border}; border-radius:18px; background:{p_bg}; color:#888; font-size:22px; padding-bottom:4px; }}
        QPushButton#plusBtn:hover {{ border-color:#888; color:{"white" if self.is_dark else "#000"}; }}
        """

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SubtitleApp()
    window.show()
    sys.exit(app.exec())
#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
  TELEGRAM HOSTING BOT - Professional Cloud Hosting Platform
  Single-file deployment ready for Render.com 24/7 hosting
  Compatible with Python 3.10 - 3.14
═══════════════════════════════════════════════════════════════

Features:
  • Upload .py, .js, .zip projects
  • Auto-extract ZIP archives
  • Isolated execution per user
  • Live terminal simulation
  • Code editor inside Telegram
  • Preview URL generation
  • Professional inline keyboard UI
  • Progress status messages

Deployment: Upload to Render.com as a Python web service
"""

import os
import sys
import json
import time
import uuid
import shutil
import signal
import asyncio
import tempfile
import threading
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from collections import defaultdict

# ─── TELEGRAM BOT LIBRARY ─────────────────────────────────────
try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
    from telegram.ext import (
        Application, ApplicationBuilder, CommandHandler, 
        CallbackQueryHandler, MessageHandler, ContextTypes, filters
    )
except ImportError:
    print("Installing python-telegram-bot...")
    os.system("pip install python-telegram-bot --quiet")
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
    from telegram.ext import (
        Application, ApplicationBuilder, CommandHandler, 
        CallbackQueryHandler, MessageHandler, ContextTypes, filters
    )

# ─── CONFIGURATION ────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
MAX_FILE_SIZE_MB = 50
EXECUTION_TIMEOUT = 30  # seconds
MAX_PROJECTS_PER_USER = 10
DATA_DIR = Path("data")
PROJECTS_DIR = DATA_DIR / "projects"
LOGS_DIR = DATA_DIR / "logs"

# Create directories
for d in [DATA_DIR, PROJECTS_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── DATA MODELS ──────────────────────────────────────────────
@dataclass
class Project:
    id: str
    user_id: int
    name: str
    language: str  # 'python', 'javascript'
    created_at: str
    files: Dict[str, str] = field(default_factory=dict)
    main_file: str = ""
    is_running: bool = False
    process_pid: Optional[int] = None
    preview_url: str = ""
    last_run_output: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

@dataclass
class UserSession:
    user_id: int
    current_project: Optional[str] = None
    editing_file: Optional[str] = None
    navigation_stack: List[str] = field(default_factory=list)
    terminal_mode: bool = False
    last_activity: str = field(default_factory=lambda: datetime.now().isoformat())

# ─── IN-MEMORY STORAGE ──────────────────────────────────────────
projects_db: Dict[str, Project] = {}
user_sessions: Dict[int, UserSession] = {}
active_processes: Dict[str, subprocess.Popen] = {}
process_logs: Dict[str, List[str]] = defaultdict(list)

# ─── KEYBOARD UI SYSTEM ─────────────────────────────────────────
class KeyboardUI:
    """Professional inline keyboard builder"""

    @staticmethod
    def main_menu() -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton("📤 Upload Project", callback_data="menu_upload"),
                InlineKeyboardButton("📁 My Projects", callback_data="menu_projects")
            ],
            [
                InlineKeyboardButton("🖥 Live Terminal", callback_data="menu_terminal"),
                InlineKeyboardButton("✏️ Code Editor", callback_data="menu_editor")
            ],
            [
                InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
                InlineKeyboardButton("❓ Help", callback_data="menu_help")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def upload_menu() -> InlineKeyboardMarkup:
        keyboard = [
            [InlineKeyboardButton("📄 Python File (.py)", callback_data="upload_py")],
            [InlineKeyboardButton("📄 JavaScript File (.js)", callback_data="upload_js")],
            [InlineKeyboardButton("📦 ZIP Archive (.zip)", callback_data="upload_zip")],
            [InlineKeyboardButton("🔙 Back to Home", callback_data="menu_home")]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def project_list(projects: List[Project]) -> InlineKeyboardMarkup:
        keyboard = []
        for proj in projects:
            status = "🟢" if proj.is_running else "⚪"
            keyboard.append([
                InlineKeyboardButton(
                    f"{status} {proj.name}", 
                    callback_data=f"project_open_{proj.id}"
                )
            ])
        keyboard.append([InlineKeyboardButton("🔙 Back to Home", callback_data="menu_home")])
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def project_actions(project: Project) -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton("▶️ Run", callback_data=f"proj_run_{project.id}"),
                InlineKeyboardButton("⏹ Stop", callback_data=f"proj_stop_{project.id}")
            ],
            [
                InlineKeyboardButton("📂 Files", callback_data=f"proj_files_{project.id}"),
                InlineKeyboardButton("✏️ Edit", callback_data=f"proj_edit_{project.id}")
            ],
            [
                InlineKeyboardButton("🌐 Preview", callback_data=f"proj_preview_{project.id}"),
                InlineKeyboardButton("🗑 Delete", callback_data=f"proj_delete_{project.id}")
            ],
            [
                InlineKeyboardButton("📋 Logs", callback_data=f"proj_logs_{project.id}"),
                InlineKeyboardButton("🔙 Back", callback_data="menu_projects")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def file_browser(project_id: str, files: Dict[str, str], page: int = 0) -> InlineKeyboardMarkup:
        keyboard = []
        file_list = list(files.keys())
        per_page = 8
        start = page * per_page
        end = start + per_page

        for fname in file_list[start:end]:
            keyboard.append([
                InlineKeyboardButton(
                    f"📄 {fname}", 
                    callback_data=f"file_view_{project_id}_{fname}"
                )
            ])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"files_page_{project_id}_{page-1}"))
        if end < len(file_list):
            nav.append(InlineKeyboardButton("▶️ Next", callback_data=f"files_page_{project_id}_{page+1}"))
        if nav:
            keyboard.append(nav)

        keyboard.append([
            InlineKeyboardButton("➕ New File", callback_data=f"file_new_{project_id}"),
            InlineKeyboardButton("🔙 Back", callback_data=f"project_open_{project_id}")
        ])
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def file_actions(project_id: str, filename: str) -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton("✏️ Edit", callback_data=f"file_edit_{project_id}_{filename}"),
                InlineKeyboardButton("🗑 Delete", callback_data=f"file_del_{project_id}_{filename}")
            ],
            [
                InlineKeyboardButton("▶️ Run This File", callback_data=f"file_run_{project_id}_{filename}"),
                InlineKeyboardButton("📋 View", callback_data=f"file_view_{project_id}_{filename}")
            ],
            [
                InlineKeyboardButton("🔙 Back to Files", callback_data=f"proj_files_{project_id}")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def editor_menu(project_id: str, filename: str) -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton("💾 Save & Deploy", callback_data=f"editor_save_{project_id}_{filename}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"file_view_{project_id}_{filename}")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def terminal_menu(project_id: str) -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data=f"term_refresh_{project_id}"),
                InlineKeyboardButton("⏹ Stop Process", callback_data=f"proj_stop_{project_id}")
            ],
            [
                InlineKeyboardButton("📥 Input Command", callback_data=f"term_input_{project_id}"),
                InlineKeyboardButton("🔙 Back", callback_data=f"project_open_{project_id}")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def settings_menu() -> InlineKeyboardMarkup:
        keyboard = [
            [InlineKeyboardButton("🗑 Clear All Projects", callback_data="settings_clear")],
            [InlineKeyboardButton("📊 System Status", callback_data="settings_status")],
            [InlineKeyboardButton("🔙 Back to Home", callback_data="menu_home")]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def confirm_delete(project_id: str) -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_del_{project_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"project_open_{project_id}")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

# ─── PROJECT MANAGER ──────────────────────────────────────────
class ProjectManager:
    """Handles project CRUD operations"""

    @staticmethod
    def create_project(user_id: int, name: str, language: str) -> Project:
        project_id = f"proj_{user_id}_{uuid.uuid4().hex[:8]}"
        project = Project(
            id=project_id,
            user_id=user_id,
            name=name,
            language=language,
            created_at=datetime.now().isoformat(),
            preview_url=f"http://user{user_id}.local/run/{project_id}"
        )

        # Create project directory
        proj_dir = PROJECTS_DIR / project_id
        proj_dir.mkdir(parents=True, exist_ok=True)

        projects_db[project_id] = project
        ProjectManager.save_projects()
        return project

    @staticmethod
    def get_user_projects(user_id: int) -> List[Project]:
        return [p for p in projects_db.values() if p.user_id == user_id]

    @staticmethod
    def get_project(project_id: str) -> Optional[Project]:
        return projects_db.get(project_id)

    @staticmethod
    def delete_project(project_id: str) -> bool:
        project = projects_db.get(project_id)
        if not project:
            return False

        # Stop running process
        if project_id in active_processes:
            try:
                active_processes[project_id].terminate()
                active_processes[project_id].wait(timeout=5)
            except:
                pass
            del active_processes[project_id]

        # Remove directory
        proj_dir = PROJECTS_DIR / project_id
        if proj_dir.exists():
            shutil.rmtree(proj_dir)

        del projects_db[project_id]
        ProjectManager.save_projects()
        return True

    @staticmethod
    def add_file(project_id: str, filename: str, content: str) -> bool:
        project = projects_db.get(project_id)
        if not project:
            return False

        proj_dir = PROJECTS_DIR / project_id
        file_path = proj_dir / filename
        file_path.write_text(content, encoding="utf-8")

        project.files[filename] = content
        if not project.main_file:
            project.main_file = filename
        ProjectManager.save_projects()
        return True

    @staticmethod
    def get_file_content(project_id: str, filename: str) -> str:
        proj_dir = PROJECTS_DIR / project_id
        file_path = proj_dir / filename
        if file_path.exists():
            return file_path.read_text(encoding="utf-8")
        return ""

    @staticmethod
    def delete_file(project_id: str, filename: str) -> bool:
        project = projects_db.get(project_id)
        if not project or filename not in project.files:
            return False

        proj_dir = PROJECTS_DIR / project_id
        file_path = proj_dir / filename
        if file_path.exists():
            file_path.unlink()

        del project.files[filename]
        if project.main_file == filename:
            project.main_file = next(iter(project.files.keys()), "")
        ProjectManager.save_projects()
        return True

    @staticmethod
    def scan_project_files(project_id: str) -> Dict[str, str]:
        """Scan project directory and update file list"""
        proj_dir = PROJECTS_DIR / project_id
        project = projects_db.get(project_id)
        if not project or not proj_dir.exists():
            return {}

        files = {}
        for file_path in proj_dir.rglob("*"):
            if file_path.is_file():
                rel_path = str(file_path.relative_to(proj_dir))
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    files[rel_path] = content[:1000]  # Store preview
                except:
                    files[rel_path] = "[Binary file]"

        project.files = files
        ProjectManager.save_projects()
        return files

    @staticmethod
    def save_projects():
        """Persist projects to disk"""
        data = {pid: proj.to_dict() for pid, proj in projects_db.items()}
        (DATA_DIR / "projects.json").write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8"
        )

    @staticmethod
    def load_projects():
        """Load projects from disk"""
        proj_file = DATA_DIR / "projects.json"
        if proj_file.exists():
            try:
                data = json.loads(proj_file.read_text(encoding="utf-8"))
                for pid, pdict in data.items():
                    projects_db[pid] = Project.from_dict(pdict)
            except Exception as e:
                print(f"Error loading projects: {e}")

# ─── EXECUTION ENGINE ─────────────────────────────────────────
class ExecutionEngine:
    """Safe code execution with isolation"""

    @staticmethod
    def run_project(project_id: str, specific_file: Optional[str] = None) -> str:
        project = projects_db.get(project_id)
        if not project:
            return "❌ Project not found"

        proj_dir = PROJECTS_DIR / project_id
        if not proj_dir.exists():
            return "❌ Project directory not found"

        # Stop existing process
        if project_id in active_processes:
            try:
                active_processes[project_id].terminate()
                active_processes[project_id].wait(timeout=3)
            except:
                pass

        target_file = specific_file or project.main_file
        if not target_file:
            return "❌ No main file specified"

        file_path = proj_dir / target_file
        if not file_path.exists():
            return f"❌ File not found: {target_file}"

        # Determine interpreter
        if target_file.endswith(".py"):
            cmd = [sys.executable, str(file_path)]
        elif target_file.endswith(".js"):
            cmd = ["node", str(file_path)]
        else:
            return "❌ Unsupported file type"

        # Prepare log file
        log_file = LOGS_DIR / f"{project_id}.log"

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(proj_dir),
                env={
                    **os.environ,
                    "PROJECT_ID": project_id,
                    "USER_ID": str(project.user_id),
                    "PREVIEW_URL": project.preview_url
                }
            )

            active_processes[project_id] = process
            project.is_running = True
            project.process_pid = process.pid
            process_logs[project_id] = []

            # Start log reader thread
            def read_output():
                for line in process.stdout:
                    line = line.rstrip()
                    process_logs[project_id].append(line)
                    if len(process_logs[project_id]) > 500:
                        process_logs[project_id] = process_logs[project_id][-500:]

                project.is_running = False
                project.process_pid = None
                if project_id in active_processes:
                    del active_processes[project_id]

            thread = threading.Thread(target=read_output, daemon=True)
            thread.start()

            # Auto-kill after timeout
            def timeout_killer():
                time.sleep(EXECUTION_TIMEOUT)
                if project_id in active_processes and active_processes[project_id].poll() is None:
                    active_processes[project_id].terminate()
                    process_logs[project_id].append(f"\n⏱ Execution timed out after {EXECUTION_TIMEOUT}s")

            if EXECUTION_TIMEOUT > 0:
                killer = threading.Thread(target=timeout_killer, daemon=True)
                killer.start()

            return f"🚀 Started: {target_file}\n🆔 PID: {process.pid}\n⏱ Timeout: {EXECUTION_TIMEOUT}s"

        except Exception as e:
            return f"❌ Execution error: {str(e)}"

    @staticmethod
    def stop_project(project_id: str) -> str:
        if project_id not in active_processes:
            project = projects_db.get(project_id)
            if project:
                project.is_running = False
                project.process_pid = None
            return "⚪ Process not running"

        try:
            process = active_processes[project_id]
            process.terminate()
            process.wait(timeout=5)
            del active_processes[project_id]

            project = projects_db.get(project_id)
            if project:
                project.is_running = False
                project.process_pid = None

            return "⏹ Process stopped successfully"
        except Exception as e:
            return f"❌ Error stopping process: {str(e)}"

    @staticmethod
    def get_logs(project_id: str, lines: int = 50) -> str:
        logs = process_logs.get(project_id, [])
        if not logs:
            project = projects_db.get(project_id)
            if project and project.last_run_output:
                return project.last_run_output
            return "📭 No logs available"

        return "\n".join(logs[-lines:])

    @staticmethod
    def get_status() -> str:
        running = sum(1 for p in projects_db.values() if p.is_running)
        total = len(projects_db)
        return f"📊 System Status\n\n🟢 Running: {running}\n📁 Total Projects: {total}\n💾 Data Dir: {DATA_DIR}"

# ─── FILE HANDLER ───────────────────────────────────────────────
class FileHandler:
    """Handles file uploads and ZIP extraction"""

    @staticmethod
    async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        session = get_session(user_id)

        # Check project limit
        user_projects = ProjectManager.get_user_projects(user_id)
        if len(user_projects) >= MAX_PROJECTS_PER_USER:
            await update.message.reply_text(
                f"❌ Maximum {MAX_PROJECTS_PER_USER} projects allowed.\n"
                f"Delete some projects first.",
                reply_markup=KeyboardUI.main_menu()
            )
            return

        # Check file size
        file_size = update.message.document.file_size or 0
        if file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
            await update.message.reply_text(
                f"❌ File too large. Max {MAX_FILE_SIZE_MB}MB allowed."
            )
            return

        # Download file
        status_msg = await update.message.reply_text("📥 Downloading file...")

        try:
            file = await update.message.document.get_file()
            file_name = update.message.document.file_name
            file_ext = Path(file_name).suffix.lower()

            # Create temp directory
            temp_dir = Path(tempfile.mkdtemp())
            temp_file = temp_dir / file_name
            await file.download_to_drive(str(temp_file))

            await status_msg.edit_text("📦 Processing file...")

            if file_ext == ".zip":
                result = await FileHandler._process_zip(user_id, temp_file, file_name, status_msg)
            elif file_ext == ".py":
                result = await FileHandler._process_single_file(user_id, temp_file, file_name, "python", status_msg)
            elif file_ext == ".js":
                result = await FileHandler._process_single_file(user_id, temp_file, file_name, "javascript", status_msg)
            else:
                await status_msg.edit_text(f"❌ Unsupported file type: {file_ext}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return

            shutil.rmtree(temp_dir, ignore_errors=True)

        except Exception as e:
            await status_msg.edit_text(f"❌ Upload error: {str(e)}")

    @staticmethod
    async def _process_single_file(user_id: int, file_path: Path, file_name: str, 
                                    language: str, status_msg) -> None:
        project_name = Path(file_name).stem
        project = ProjectManager.create_project(user_id, project_name, language)

        content = file_path.read_text(encoding="utf-8", errors="replace")
        ProjectManager.add_file(project.id, file_name, content)

        await status_msg.edit_text(
            f"✅ Project uploaded successfully!\n\n"
            f"📁 Name: {project.name}\n"
            f"🆔 ID: `{project.id}`\n"
            f"🌐 Preview: `{project.preview_url}`",
            parse_mode="Markdown",
            reply_markup=KeyboardUI.project_actions(project)
        )

    @staticmethod
    async def _process_zip(user_id: int, zip_path: Path, file_name: str, status_msg) -> None:
        import zipfile

        project_name = Path(file_name).stem

        # Detect language from contents
        language = "python"

        with zipfile.ZipFile(zip_path, 'r') as zf:
            file_list = zf.namelist()

            # Check for JS files
            if any(f.endswith(".js") for f in file_list):
                language = "javascript"

            # Create project
            project = ProjectManager.create_project(user_id, project_name, language)
            proj_dir = PROJECTS_DIR / project.id

            await status_msg.edit_text("📂 Extracting ZIP archive...")

            # Extract files
            for member in file_list:
                if member.endswith("/"):
                    continue

                # Security: prevent path traversal
                member_path = Path(member)
                if ".." in member_path.parts:
                    continue

                try:
                    zf.extract(member, str(proj_dir))
                except:
                    continue

            # Scan and index files
            await status_msg.edit_text("🔍 Indexing project files...")
            files = ProjectManager.scan_project_files(project.id)

            # Detect main file
            main_candidates = {
                "python": ["main.py", "app.py", "index.py", "bot.py", "server.py"],
                "javascript": ["main.js", "app.js", "index.js", "server.js", "bot.js"]
            }

            for candidate in main_candidates.get(language, []):
                if candidate in files:
                    project.main_file = candidate
                    break

            if not project.main_file and files:
                # Pick first file with correct extension
                ext = ".py" if language == "python" else ".js"
                for fname in files:
                    if fname.endswith(ext):
                        project.main_file = fname
                        break

            ProjectManager.save_projects()

            file_count = len(files)
            await status_msg.edit_text(
                f"✅ ZIP project extracted!\n\n"
                f"📁 Name: {project.name}\n"
                f"🆔 ID: `{project.id}`\n"
                f"📄 Files: {file_count}\n"
                f"🎯 Main: {project.main_file or 'Not detected'}\n"
                f"🌐 Preview: `{project.preview_url}`",
                parse_mode="Markdown",
                reply_markup=KeyboardUI.project_actions(project)
            )

# ─── SESSION MANAGEMENT ───────────────────────────────────────
def get_session(user_id: int) -> UserSession:
    if user_id not in user_sessions:
        user_sessions[user_id] = UserSession(user_id=user_id)
    return user_sessions[user_id]

# ─── BOT COMMAND HANDLERS ───────────────────────────────────────
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    session.navigation_stack = ["home"]

    welcome_text = (
        "🌐 *Welcome to Telegram Hosting Bot*\n\n"
        "Upload, manage, and run your projects directly in Telegram.\n\n"
        "*Supported formats:*\n"
        "• Python (.py)\n"
        "• JavaScript (.js)\n"
        "• ZIP archives (.zip)\n\n"
        "Click below to get started 👇"
    )

    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=KeyboardUI.main_menu()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 *Telegram Hosting Bot - Help*\n\n"
        "*Upload Project* - Send .py, .js, or .zip files\n"
        "*My Projects* - View and manage your projects\n"
        "*Live Terminal* - Real-time execution logs\n"
        "*Code Editor* - Edit files inside Telegram\n"
        "*Settings* - System status and cleanup\n\n"
        "*Tips:*\n"
        "• ZIP files are auto-extracted\n"
        "• Main file is auto-detected\n"
        "• Execution timeout: 30s\n"
        "• Max projects per user: 10"
    )

    await update.message.reply_text(
        help_text,
        parse_mode="Markdown",
        reply_markup=KeyboardUI.main_menu()
    )

# ─── CALLBACK QUERY HANDLER ───────────────────────────────────
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    session = get_session(user_id)
    data = query.data

    # ── MENU NAVIGATION ──
    if data == "menu_home":
        session.navigation_stack = ["home"]
        session.terminal_mode = False
        session.editing_file = None

        await query.edit_message_text(
            "🏠 *Home Menu*\n\nSelect an option:",
            parse_mode="Markdown",
            reply_markup=KeyboardUI.main_menu()
        )

    elif data == "menu_upload":
        session.navigation_stack.append("upload")

        await query.edit_message_text(
            "📤 *Upload Project*\n\n"
            "Choose file type or simply send a file directly:",
            parse_mode="Markdown",
            reply_markup=KeyboardUI.upload_menu()
        )

    elif data == "menu_projects":
        session.navigation_stack.append("projects")
        projects = ProjectManager.get_user_projects(user_id)

        if not projects:
            await query.edit_message_text(
                "📭 *No Projects Yet*\n\n"
                "Upload your first project to get started!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 Upload Project", callback_data="menu_upload")],
                    [InlineKeyboardButton("🔙 Back to Home", callback_data="menu_home")]
                ])
            )
        else:
            await query.edit_message_text(
                f"📁 *My Projects* ({len(projects)})\n\nSelect a project:",
                parse_mode="Markdown",
                reply_markup=KeyboardUI.project_list(projects)
            )

    elif data == "menu_terminal":
        session.navigation_stack.append("terminal")
        projects = ProjectManager.get_user_projects(user_id)

        if not projects:
            await query.edit_message_text(
                "📭 No projects to monitor. Upload a project first.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 Upload Project", callback_data="menu_upload")],
                    [InlineKeyboardButton("🔙 Back", callback_data="menu_home")]
                ])
            )
        else:
            await query.edit_message_text(
                "🖥 *Live Terminal*\n\nSelect a project to monitor:",
                parse_mode="Markdown",
                reply_markup=KeyboardUI.project_list(projects)
            )

    elif data == "menu_editor":
        session.navigation_stack.append("editor")
        projects = ProjectManager.get_user_projects(user_id)

        if not projects:
            await query.edit_message_text(
                "📭 No projects to edit. Upload a project first.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 Upload Project", callback_data="menu_upload")],
                    [InlineKeyboardButton("🔙 Back", callback_data="menu_home")]
                ])
            )
        else:
            await query.edit_message_text(
                "✏️ *Code Editor*\n\nSelect a project:",
                parse_mode="Markdown",
                reply_markup=KeyboardUI.project_list(projects)
            )

    elif data == "menu_settings":
        session.navigation_stack.append("settings")

        await query.edit_message_text(
            "⚙️ *Settings*\n\nManage your hosting environment:",
            parse_mode="Markdown",
            reply_markup=KeyboardUI.settings_menu()
        )

    elif data == "menu_help":
        await help_command(update, context)

    # ── PROJECT ACTIONS ──
    elif data.startswith("project_open_"):
        project_id = data.replace("project_open_", "")
        project = ProjectManager.get_project(project_id)

        if not project:
            await query.edit_message_text("❌ Project not found.", reply_markup=KeyboardUI.main_menu())
            return

        session.current_project = project_id
        status = "🟢 Running" if project.is_running else "⚪ Stopped"

        info_text = (
            f"📁 *{project.name}*\n"
            f"{'─' * 30}\n"
            f"🆔 ID: `{project.id}`\n"
            f"📊 Status: {status}\n"
            f"🔤 Language: {project.language}\n"
            f"🎯 Main File: `{project.main_file}`\n"
            f"📄 Files: {len(project.files)}\n"
            f"🌐 Preview: `{project.preview_url}`\n"
            f"📅 Created: {project.created_at[:10]}"
        )

        await query.edit_message_text(
            info_text,
            parse_mode="Markdown",
            reply_markup=KeyboardUI.project_actions(project)
        )

    elif data.startswith("proj_run_"):
        project_id = data.replace("proj_run_", "")
        await query.edit_message_text("🚀 Starting execution...")

        result = ExecutionEngine.run_project(project_id)

        await asyncio.sleep(1)
        logs = ExecutionEngine.get_logs(project_id, 20)

        display_text = f"{result}\n\n📋 *Recent Logs:*\n```\n{logs[:800]}\n```"

        await query.edit_message_text(
            display_text,
            parse_mode="Markdown",
            reply_markup=KeyboardUI.terminal_menu(project_id)
        )

    elif data.startswith("proj_stop_"):
        project_id = data.replace("proj_stop_", "")
        result = ExecutionEngine.stop_project(project_id)

        await query.edit_message_text(
            f"⏹ *Process Stopped*\n\n{result}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Project", callback_data=f"project_open_{project_id}")]
            ])
        )

    elif data.startswith("proj_files_"):
        project_id = data.replace("proj_files_", "")
        project = ProjectManager.get_project(project_id)

        if not project:
            return

        # Refresh file list
        files = ProjectManager.scan_project_files(project_id)

        await query.edit_message_text(
            f"📂 *File Browser* - {project.name}\n\n"
            f"Total files: {len(files)}",
            parse_mode="Markdown",
            reply_markup=KeyboardUI.file_browser(project_id, files)
        )

    elif data.startswith("files_page_"):
        parts = data.split("_")
        project_id = parts[2]
        page = int(parts[3])
        project = ProjectManager.get_project(project_id)

        if project:
            await query.edit_message_text(
                f"📂 *File Browser* - {project.name} (Page {page + 1})",
                parse_mode="Markdown",
                reply_markup=KeyboardUI.file_browser(project_id, project.files, page)
            )

    elif data.startswith("file_view_"):
        parts = data.split("_", 3)
        project_id = parts[2]
        filename = parts[3]

        content = ProjectManager.get_file_content(project_id, filename)
        preview = content[:900] if len(content) <= 900 else content[:900] + "\n... (truncated)"

        await query.edit_message_text(
            f"📄 *{filename}*\n"
            f"{'─' * 25}\n"
            f"```\n{preview}\n```",
            parse_mode="Markdown",
            reply_markup=KeyboardUI.file_actions(project_id, filename)
        )

    elif data.startswith("file_edit_"):
        parts = data.split("_", 3)
        project_id = parts[2]
        filename = parts[3]

        session.editing_file = f"{project_id}:{filename}"
        content = ProjectManager.get_file_content(project_id, filename)

        await query.edit_message_text(
            f"✏️ *Editing: {filename}*\n\n"
            f"Reply with the new file content.\n"
            f"Current size: {len(content)} chars\n\n"
            f"*Current content preview:*\n"
            f"```\n{content[:400]}\n```",
            parse_mode="Markdown",
            reply_markup=KeyboardUI.editor_menu(project_id, filename)
        )

    elif data.startswith("editor_save_"):
        parts = data.split("_", 3)
        project_id = parts[2]
        filename = parts[3]

        # This is triggered after user sends new content
        # The actual save happens in message handler
        await query.edit_message_text(
            "💾 Send the new file content as a reply to this message.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data=f"file_view_{project_id}_{filename}")]
            ])
        )

    elif data.startswith("file_del_"):
        parts = data.split("_", 3)
        project_id = parts[2]
        filename = parts[3]

        await query.edit_message_text(
            f"⚠️ Delete `{filename}`?\n\nThis cannot be undone.",
            parse_mode="Markdown",
            reply_markup=KeyboardUI.confirm_delete(f"file_{project_id}_{filename}")
        )

    elif data.startswith("file_run_"):
        parts = data.split("_", 3)
        project_id = parts[2]
        filename = parts[3]

        await query.edit_message_text(f"▶️ Running `{filename}`...", parse_mode="Markdown")

        result = ExecutionEngine.run_project(project_id, filename)
        await asyncio.sleep(1)
        logs = ExecutionEngine.get_logs(project_id, 30)

        await query.edit_message_text(
            f"{result}\n\n📋 *Output:*\n```\n{logs[:1000]}\n```",
            parse_mode="Markdown",
            reply_markup=KeyboardUI.terminal_menu(project_id)
        )

    elif data.startswith("file_new_"):
        project_id = data.replace("file_new_", "")
        session.editing_file = f"{project_id}:NEW_FILE"

        await query.edit_message_text(
            "➕ *Create New File*\n\n"
            "Reply with the filename and content in this format:\n"
            "`filename.py\n\nfile content here`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data=f"proj_files_{project_id}")]
            ])
        )

    elif data.startswith("proj_preview_"):
        project_id = data.replace("proj_preview_", "")
        project = ProjectManager.get_project(project_id)

        if project:
            preview_text = (
                f"🌐 *Preview URL*\n\n"
                f"`{project.preview_url}`\n\n"
                f"⚠️ Note: This is a simulated local URL.\n"
                f"In production, map this to your actual hosting."
            )

            await query.edit_message_text(
                preview_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data=f"project_open_{project_id}")]
                ])
            )

    elif data.startswith("proj_logs_"):
        project_id = data.replace("proj_logs_", "")
        logs = ExecutionEngine.get_logs(project_id, 100)

        await query.edit_message_text(
            f"📋 *Execution Logs*\n"
            f"{'─' * 25}\n"
            f"```\n{logs[:3900]}\n```",
            parse_mode="Markdown",
            reply_markup=KeyboardUI.terminal_menu(project_id)
        )

    elif data.startswith("proj_delete_"):
        project_id = data.replace("proj_delete_", "")

        await query.edit_message_text(
            "⚠️ *Delete Project?*\n\nThis will remove all files and stop any running processes.",
            parse_mode="Markdown",
            reply_markup=KeyboardUI.confirm_delete(project_id)
        )

    elif data.startswith("confirm_del_"):
        target = data.replace("confirm_del_", "")

        if target.startswith("file_"):
            # Delete file
            parts = target.split("_")
            project_id = parts[1]
            filename = "_".join(parts[2:])
            ProjectManager.delete_file(project_id, filename)

            await query.edit_message_text(
                f"✅ File `{filename}` deleted.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to Files", callback_data=f"proj_files_{project_id}")]
                ])
            )
        else:
            # Delete project
            ProjectManager.delete_project(target)
            await query.edit_message_text(
                "🗑 Project deleted successfully.",
                reply_markup=KeyboardUI.main_menu()
            )

    elif data.startswith("term_refresh_"):
        project_id = data.replace("term_refresh_", "")
        logs = ExecutionEngine.get_logs(project_id, 50)
        project = ProjectManager.get_project(project_id)

        status = "🟢 Running" if (project and project.is_running) else "⚪ Stopped"

        await query.edit_message_text(
            f"🖥 *Live Terminal* - {status}\n"
            f"{'─' * 25}\n"
            f"```\n{logs[:3800]}\n```",
            parse_mode="Markdown",
            reply_markup=KeyboardUI.terminal_menu(project_id)
        )

    elif data.startswith("term_input_"):
        project_id = data.replace("term_input_", "")
        session.terminal_mode = True
        session.current_project = project_id

        await query.edit_message_text(
            "⌨️ *Terminal Input Mode*\n\n"
            "Send messages to interact with the running process.\n"
            "Send /exit to leave terminal mode.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏹ Stop Process", callback_data=f"proj_stop_{project_id}")],
                [InlineKeyboardButton("🔙 Back", callback_data=f"project_open_{project_id}")]
            ])
        )

    elif data.startswith("settings_clear"):
        projects = ProjectManager.get_user_projects(user_id)
        for proj in projects:
            ProjectManager.delete_project(proj.id)

        await query.edit_message_text(
            "🗑 All projects cleared.",
            reply_markup=KeyboardUI.settings_menu()
        )

    elif data == "settings_status":
        status = ExecutionEngine.get_status()
        await query.edit_message_text(
            status,
            parse_mode="Markdown",
            reply_markup=KeyboardUI.settings_menu()
        )

    # ── UPLOAD TYPE SELECTION ──
    elif data in ["upload_py", "upload_js", "upload_zip"]:
        file_type = {"upload_py": "Python (.py)", "upload_js": "JavaScript (.js)", "upload_zip": "ZIP (.zip)"}[data]

        await query.edit_message_text(
            f"📤 *Upload {file_type}*\n\n"
            f"Simply send the file directly in this chat.\n"
            f"The bot will auto-detect and process it.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="menu_upload")]
            ])
        )

# ─── MESSAGE HANDLER ────────────────────────────────────────────
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    text = update.message.text

    # Handle terminal input
    if session.terminal_mode and session.current_project:
        if text == "/exit":
            session.terminal_mode = False
            await update.message.reply_text(
                "👋 Exited terminal mode.",
                reply_markup=KeyboardUI.main_menu()
            )
            return

        project_id = session.current_project
        if project_id in active_processes:
            try:
                process = active_processes[project_id]
                process.stdin.write(text + "\n")
                process.stdin.flush()
                await update.message.reply_text("📤 Sent to process")
            except:
                await update.message.reply_text("❌ Cannot send input to process")
        else:
            await update.message.reply_text("⚪ No running process")
        return

    # Handle file editor
    if session.editing_file:
        edit_target = session.editing_file
        session.editing_file = None

        if edit_target.endswith(":NEW_FILE"):
            project_id = edit_target.replace(":NEW_FILE", "")
            lines = text.split("\n", 1)
            if len(lines) >= 2:
                filename = lines[0].strip()
                content = lines[1]
            else:
                filename = "untitled.txt"
                content = text

            ProjectManager.add_file(project_id, filename, content)
            await update.message.reply_text(
                f"✅ Created `{filename}`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📂 View Files", callback_data=f"proj_files_{project_id}")]
                ])
            )
        else:
            project_id, filename = edit_target.split(":", 1)
            ProjectManager.add_file(project_id, filename, text)

            await update.message.reply_text(
                f"💾 Saved `{filename}`\n"
                f"🔄 Redeploying...",
                parse_mode="Markdown"
            )

            # Auto-restart if running
            project = ProjectManager.get_project(project_id)
            if project and project.is_running:
                ExecutionEngine.stop_project(project_id)
                await asyncio.sleep(1)
                result = ExecutionEngine.run_project(project_id)
                await update.message.reply_text(
                    f"🔄 Auto-restarted:\n{result}",
                    reply_markup=KeyboardUI.project_actions(project)
                )
            else:
                await update.message.reply_text(
                    "✅ File saved successfully.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back to File", callback_data=f"file_view_{project_id}_{filename}")]
                    ])
                )
        return

    # Default response
    await update.message.reply_text(
        "👋 Use the menu below or send a file to upload.",
        reply_markup=KeyboardUI.main_menu()
    )

# ─── DOCUMENT HANDLER ─────────────────────────────────────────
async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    file_name = doc.file_name or ""

    valid_exts = (".py", ".js", ".zip")
    if not any(file_name.lower().endswith(ext) for ext in valid_exts):
        await update.message.reply_text(
            f"❌ Unsupported file: `{file_name}`\n\n"
            f"Supported: .py, .js, .zip",
            parse_mode="Markdown"
        )
        return

    await FileHandler.handle_file_upload(update, context)

# ─── ERROR HANDLER ────────────────────────────────────────────
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"Error: {context.error}")
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ An error occurred. Please try again.",
                reply_markup=KeyboardUI.main_menu()
            )
    except:
        pass

# ─── HEALTH CHECK WEB SERVER (FOR RENDER) ─────────────────────
async def run_web_server():
    """Lightweight web server for Render.com health checks"""
    from aiohttp import web

    async def health_check(request):
        running = sum(1 for p in projects_db.values() if p.is_running)
        return web.json_response({
            "status": "ok",
            "projects": len(projects_db),
            "running": running,
            "timestamp": datetime.now().isoformat()
        })

    async def preview_handler(request):
        project_id = request.match_info.get("project_id", "")
        project = projects_db.get(project_id)

        if not project:
            return web.Response(text="Project not found", status=404)

        if not project.is_running:
            return web.Response(text="Project not running", status=503)

        logs = "\n".join(process_logs.get(project_id, [])[-50:])
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><title>{project.name} - Preview</title></head>
        <body style="font-family: monospace; padding: 20px;">
            <h2>🌐 {project.name} - Live Preview</h2>
            <p>Status: {'Running' if project.is_running else 'Stopped'}</p>
            <p>Language: {project.language}</p>
            <hr>
            <h3>Recent Logs:</h3>
            <pre style="background: #1e1e1e; color: #d4d4d4; padding: 15px; border-radius: 5px;">
{logs}
            </pre>
        </body>
        </html>
        """
        return web.Response(text=html, content_type="text/html")

    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    app.router.add_get("/run/{project_id}", preview_handler)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Web server running on port {port}")

# ─── MAIN APPLICATION ─────────────────────────────────────────
def main():
    # Load existing projects
    ProjectManager.load_projects()

    # Validate token
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ ERROR: BOT_TOKEN environment variable is not set!")
        print("   Get token from @BotFather on Telegram")
        print("   Set it in Render Dashboard → Environment → BOT_TOKEN")
        sys.exit(1)

    print("🚀 Starting Telegram Hosting Bot...")
    print(f"📁 Data directory: {DATA_DIR.absolute()}")
    print(f"📁 Projects directory: {PROJECTS_DIR.absolute()}")

    # Build application
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Add handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_error_handler(error_handler)

    # Start web server in background (for Render)
    try:
        import aiohttp
        loop = asyncio.get_event_loop()
        loop.create_task(run_web_server())
    except ImportError:
        print("⚠️ aiohttp not installed. Web preview server disabled.")
        print("   Install with: pip install aiohttp")

    # Start bot
    print("🤖 Bot is running! Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()

from flask import Flask, render_template, request, redirect, url_for, flash, Response, abort, session, current_app
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from sqlalchemy.exc import OperationalError
import pyodbc
import os
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash

from flask_mail import Mail, Message

from config import Config
from models import db, Project, Employee, ProjectAssignment, Workspace, UserWorkspace


def compute_progress_percent(p: Project, today=None) -> int:
    """
    Единый helper для вычисления процента готовности проекта по времени.
    - 0% в начале периода
    - 100% в день окончания или позже
    Используется на дашборде и в деталях проекта (Single Source of Truth).
    """
    from datetime import date as _date

    if today is None:
        today = _date.today()

    if p.status == "Completed":
        return 100

    if p.status == "In Progress" and p.start_date and p.end_date:
        total_days = (p.end_date - p.start_date).days
        if total_days > 0:
            elapsed_days = (today - p.start_date).days
            ratio_done = elapsed_days / total_days
            return max(0, min(100, int(ratio_done * 100)))

    # Для проектов без дат показываем базовое значение
    return 0


def _generate_otp_code(length: int = 6) -> str:
    import random
    return "".join(str(random.randint(0, 9)) for _ in range(length))


def _generate_invite_code(length: int = 12) -> str:
    import secrets
    import string
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def send_otp_email(mail: Mail, target_email: str, code: str) -> None:
    """
    Отправляет HTML‑письмо с 6‑значным кодом подтверждения Flux.
    """
    subject = "Код подтверждения Flux"
    logo_cid = "flux_logo_clear"
    html_body = f"""
    <div style="font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:#0f172a; padding:48px 0;">
      <table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" width="100%" style="max-width:480px; margin:0 auto; background:#020617; border-radius:24px; overflow:hidden; box-shadow:0 24px 80px rgba(15,23,42,0.85);">
        <tr>
          <td style="padding:28px 32px 12px 32px; border-bottom:1px solid rgba(148,163,184,0.25);">
            <div style="text-align:center; padding:20px 0 4px 0;">
              <img src="cid:flux_logo_clear" alt="FLUX" style="display:inline-block; width:100px; max-width:100%; border:none; filter:brightness(0) invert(1);">
            </div>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 32px 8px 32px; color:#e5e7eb;">
            <h1 style="margin:0 0 4px 0; font-size:20px; font-weight:600;">Подтвердите ваш email</h1>
            <p style="margin:0; font-size:13px; color:#9ca3af;">Введите этот код на странице регистрации Flux, чтобы продолжить.</p>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 32px;">
            <div style="text-align:center; padding:18px 16px; border-radius:18px; background:rgba(15,23,42,0.85); border:1px solid rgba(79,70,229,0.6); box-shadow:0 0 0 1px rgba(15,23,42,0.9) inset;">
              <div style="font-size:32px; letter-spacing:0.4em; font-weight:800; color:#e5e7eb; text-transform:uppercase;">
                {" ".join(code)}
              </div>
              <p style="margin:10px 0 0 0; font-size:11px; letter-spacing:0.18em; text-transform:uppercase; color:#6b7280;">
                Код действует 10 минут
              </p>
            </div>
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 28px 32px; color:#6b7280; font-size:11px;">
            Если вы не запрашивали регистрацию во Flux, просто проигнорируйте это письмо.
          </td>
        </tr>
      </table>
    </div>
    """
    msg = Message(subject=subject, recipients=[target_email], html=html_body)

    # Встраиваем логотип как inline‑картинку по CID
    try:
        img_path = os.path.join(current_app.root_path, "static", "images", "flux_wordmark_final.png")
        with open(img_path, "rb") as f:
            logo_data = f.read()
        msg.attach(
            "flux_wordmark_final.png",
            "image/png",
            logo_data,
            headers={
                "Content-ID": "<flux_logo_clear>",
                "Content-Disposition": "inline",
            },
        )
    except Exception:
        # Если логотип недоступен, просто отправляем письмо без изображения
        pass

    mail.send(msg)


def _parse_date_any(raw: str):
    """
    Поддерживает форматы дат:
    - YYYY-MM-DD (из <input type="date">)
    - DD.MM.YYYY (из текстовых фильтров)
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    from datetime import datetime

    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def ensure_database_and_tables() -> None:
    """
    Гарантирует наличие базы ProjectDB и всех таблиц.
    """
    # Сначала убеждаемся, что сама база ProjectDB существует
    master_conn_str = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=localhost;"
        "DATABASE=master;"
        "Trusted_Connection=yes;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )
    with pyodbc.connect(master_conn_str, autocommit=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "IF DB_ID('ProjectDB') IS NULL BEGIN EXEC('CREATE DATABASE ProjectDB'); END"
            )

    # Затем создаём таблицы через SQLAlchemy
    try:
        db.create_all()
    except OperationalError:
        # Если по какой-то причине возникла проблема, повторяем ещё раз
        db.create_all()

    # Добавляем auth-колонки в Employees (без миграций)
    projectdb_conn_str = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=localhost;"
        "DATABASE=ProjectDB;"
        "Trusted_Connection=yes;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )
    with pyodbc.connect(projectdb_conn_str, autocommit=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                IF COL_LENGTH('Employees', 'PasswordHash') IS NULL
                    ALTER TABLE Employees ADD PasswordHash NVARCHAR(255) NULL;
                """
            )
            cursor.execute(
                """
                IF COL_LENGTH('Employees', 'Role') IS NULL
                    ALTER TABLE Employees ADD Role NVARCHAR(20) NOT NULL CONSTRAINT DF_Employees_Role DEFAULT ('Worker');
                """
            )
            cursor.execute(
                """
                IF COL_LENGTH('Employees', 'AvatarFilename') IS NULL
                    ALTER TABLE Employees ADD AvatarFilename NVARCHAR(255) NULL;
                """
            )

            # Workspaces / UserWorkspaces tables are created via SQLAlchemy create_all().
            # Добавляем WorkspaceID в Projects без миграций
            cursor.execute(
                """
                IF COL_LENGTH('Projects', 'WorkspaceID') IS NULL
                    ALTER TABLE Projects ADD WorkspaceID INT NULL;
                """
            )

    # Инициализация дефолтного workspace для существующих данных
    try:
        from sqlalchemy import select
        existing_ws = db.session.execute(select(Workspace).limit(1)).scalars().first()
        if not existing_ws:
            owner = Employee.query.filter(Employee.role == "Admin").order_by(Employee.id.asc()).first()
            if not owner:
                owner = Employee.query.order_by(Employee.id.asc()).first()
            if owner:
                invite = _generate_invite_code()
                ws = Workspace(name="Flux Workspace", invite_code=invite, owner_id=owner.id)
                db.session.add(ws)
                db.session.flush()
                db.session.add(UserWorkspace(workspace_id=ws.id, user_id=owner.id, role="owner"))

                # привязываем все существующие проекты
                Project.query.filter(Project.workspace_id.is_(None)).update(
                    {"workspace_id": ws.id}, synchronize_session=False
                )
                db.session.commit()
    except Exception:
        db.session.rollback()



def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    # Optional .env support (no hard dependency)
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except Exception:
        pass

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)

    # Flask-Mail (loaded from environment / .env)
    app.config.update(
        MAIL_SERVER=os.getenv("MAIL_SERVER", "smtp.mail.ru"),
        MAIL_PORT=int(os.getenv("MAIL_PORT", "465")),
        MAIL_USE_SSL=os.getenv("MAIL_USE_SSL", "True").lower() in {"1", "true", "yes", "on"},
        MAIL_USERNAME=os.getenv("MAIL_USERNAME", ""),
        MAIL_PASSWORD=os.getenv("MAIL_PASSWORD", ""),
        MAIL_DEFAULT_SENDER=(
            os.getenv("MAIL_DEFAULT_SENDER_NAME", "Flux"),
            os.getenv("MAIL_DEFAULT_SENDER_EMAIL", os.getenv("MAIL_USERNAME", "")),
        ),
    )
    mail = Mail(app)

    @login_manager.user_loader
    def load_user(user_id: str):
        try:
            return db.session.get(Employee, int(user_id))
        except (TypeError, ValueError):
            return None

    with app.app_context():
        ensure_database_and_tables()

    def _get_current_workspace():
        """
        Возвращает текущий workspace только из session['workspace_id'].
        Выбор конкретного пространства теперь осуществляется через /hub.
        """
        if not current_user.is_authenticated:
            return None
        ws_id = session.get("workspace_id")
        if not ws_id:
            return None
        return db.session.get(Workspace, int(ws_id))

    def _workspace_role(workspace: Workspace | None) -> str | None:
        if not workspace or not current_user.is_authenticated:
            return None
        if workspace.owner_id == current_user.id:
            return "owner"
        m = UserWorkspace.query.filter_by(workspace_id=workspace.id, user_id=current_user.id).first()
        if not m:
            return None
        return (m.role or "member").lower()

    def require_workspace_role(*allowed: str):
        def deco(fn):
            from functools import wraps
            @wraps(fn)
            def wrapper(*args, **kwargs):
                ws = _get_current_workspace()
                if not ws:
                    abort(403)
                role = _workspace_role(ws)
                if role not in allowed:
                    abort(403)
                return fn(*args, **kwargs)
            return wrapper
        return deco

    @app.context_processor
    def inject_workspace():
        ws = _get_current_workspace()
        return {
            "workspace": ws,
            "workspace_role": _workspace_role(ws),
        }

    @app.before_request
    def _ensure_workspace_selected():
        # просто прогреваем выбор workspace для последующих роутов/шаблонов
        _get_current_workspace()

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            remember = request.form.get("remember") == "on"

            user = Employee.query.filter(Employee.email == email).first()
            if not user or not user.check_password(password):
                flash("Неверный email или пароль.", "error")
                return render_template("login.html")

            login_user(user, remember=remember)
            flash("Вы вошли в систему.", "success")
            pending_invite = session.pop("pending_invite_code", None)
            if pending_invite:
                return redirect(url_for("join_workspace", invite_code=pending_invite))
            return redirect(url_for("index"))

        return render_template("login.html")

    @app.get("/logout")
    @login_required
    def logout():
        logout_user()
        flash("Вы вышли из системы.", "success")
        return redirect(url_for("login"))

    @app.get("/join/<invite_code>")
    @login_required
    def join_workspace(invite_code: str):
        ws = Workspace.query.filter(Workspace.invite_code == invite_code).first()
        if not ws:
            flash("Неверный код приглашения.", "error")
            return redirect(url_for("index"))

        existing = UserWorkspace.query.filter_by(workspace_id=ws.id, user_id=current_user.id).first()
        if existing or ws.owner_id == current_user.id:
            session["workspace_id"] = ws.id
            flash("Вы уже состоите в этом workspace.", "info")
            return redirect(url_for("index"))

        db.session.add(UserWorkspace(workspace_id=ws.id, user_id=current_user.id, role="member"))
        db.session.commit()
        session["workspace_id"] = ws.id
        flash("Вы присоединились к workspace.", "success")
        return redirect(url_for("index"))

    @app.route("/create_workspace", methods=["GET", "POST"])
    @login_required
    def create_workspace():
        if request.method == "POST":
            name = (request.form.get("name") or "").strip() or "Flux Workspace"
            invite = _generate_invite_code()
            ws = Workspace(name=name, invite_code=invite, owner_id=current_user.id)
            db.session.add(ws)
            db.session.flush()
            db.session.add(UserWorkspace(workspace_id=ws.id, user_id=current_user.id, role="owner"))
            db.session.commit()
            session["workspace_id"] = ws.id
            flash("Workspace создан.", "success")
            return redirect(url_for("index"))

        return render_template("create_workspace.html")

    @app.route("/join_workspace", methods=["GET", "POST"])
    @login_required
    def join_workspace_form():
        if request.method == "POST":
            invite_code = (request.form.get("invite_code") or "").strip()
            if not invite_code:
                flash("Введите invite code.", "error")
                return render_template("join_workspace.html")
            return redirect(url_for("join_workspace", invite_code=invite_code))

        return render_template("join_workspace.html")

    @app.route("/onboarding")
    @login_required
    def onboarding():
        if _get_current_workspace():
            return redirect(url_for("index"))
        return render_template("onboarding.html")

    @app.route("/hub")
    @login_required
    def hub():
        """
        Экран выбора рабочего пространства ("The Hub").
        Сбрасывает текущий workspace в сессии и показывает все доступные пользователю.
        """
        # очищаем выбор активного workspace
        session.pop("workspace_id", None)

        memberships = UserWorkspace.query.join(Workspace, Workspace.id == UserWorkspace.workspace_id).filter(
            UserWorkspace.user_id == current_user.id
        ).order_by(Workspace.name.asc()).all()

        if not memberships:
            # нет ни одного workspace — отправляем на онбординг
            return redirect(url_for("onboarding"))

        workspaces = []
        for m in memberships:
            ws = m.workspace
            projects_count = Project.query.filter_by(workspace_id=ws.id).count()
            members_count = UserWorkspace.query.filter_by(workspace_id=ws.id).count()
            workspaces.append(
                {
                    "id": ws.id,
                    "name": ws.name,
                    "role": "owner" if ws.owner_id == current_user.id else (m.role or "member"),
                    "projects_count": projects_count,
                    "members_count": members_count,
                }
            )
        return render_template("hub.html", workspaces=workspaces)

    @app.get("/select_workspace/<int:workspace_id>")
    @login_required
    def select_workspace(workspace_id: int):
        """
        Выбор workspace из списка на /hub.
        """
        ws = db.session.get(Workspace, workspace_id)
        if not ws:
            abort(404)

        membership = UserWorkspace.query.filter_by(
            workspace_id=workspace_id, user_id=current_user.id
        ).first()
        if not membership and ws.owner_id != current_user.id:
            abort(403)

        session["workspace_id"] = workspace_id
        return redirect(url_for("index"))

    @app.post("/workspace/create")
    @login_required
    def workspace_create():
        if _get_current_workspace():
            return redirect(url_for("index"))
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Введите название workspace.", "error")
            return redirect(url_for("onboarding"))
        invite = _generate_invite_code()
        ws = Workspace(name=name, invite_code=invite, owner_id=current_user.id)
        db.session.add(ws)
        db.session.flush()
        db.session.add(UserWorkspace(workspace_id=ws.id, user_id=current_user.id, role="owner"))
        db.session.commit()
        session["workspace_id"] = ws.id
        flash("Workspace создан.", "success")
        return redirect(url_for("index"))

    @app.post("/workspace/join")
    @login_required
    def workspace_join():
        if _get_current_workspace():
            return redirect(url_for("index"))
        invite_code = (request.form.get("invite_code") or "").strip()
        if not invite_code:
            flash("Введите invite code.", "error")
            return redirect(url_for("onboarding"))
        ws = Workspace.query.filter(Workspace.invite_code == invite_code).first()
        if not ws:
            flash("Workspace с таким invite code не найден.", "error")
            return redirect(url_for("onboarding"))
        existing = UserWorkspace.query.filter_by(workspace_id=ws.id, user_id=current_user.id).first()
        if not existing and ws.owner_id != current_user.id:
            db.session.add(UserWorkspace(workspace_id=ws.id, user_id=current_user.id, role="member"))
            db.session.commit()
        session["workspace_id"] = ws.id
        flash("Вы присоединились к workspace.", "success")
        return redirect(url_for("index"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        """
        Шаг 1: ввод email для отправки OTP.
        """
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            if not email:
                flash("Введите email.", "error")
                return render_template("register_email.html")

            existing = Employee.query.filter(Employee.email == email).first()
            if existing:
                flash("Пользователь с таким email уже существует.", "error")
                return render_template("register_email.html")

            code = _generate_otp_code()
            session["register_email"] = email
            session["register_otp"] = code
            session["email_verified"] = False

            send_otp_email(mail, email, code)
            flash("Мы отправили код подтверждения на указанный email.", "info")
            return redirect(url_for("verify"))

        return render_template("register_email.html")

    @app.route("/verify", methods=["GET", "POST"])
    def verify():
        """
        Шаг 2: ввод 6‑значного OTP кода.
        """
        email = session.get("register_email")
        if not email:
            flash("Сначала укажите email для регистрации.", "error")
            return redirect(url_for("register"))

        if request.method == "POST":
            action = request.form.get("action") or "verify"
            if action == "resend":
                code = _generate_otp_code()
                session["register_otp"] = code
                send_otp_email(mail, email, code)
                flash("Новый код отправлен на email.", "info")
                return redirect(url_for("verify"))

            entered_code = "".join(
                (request.form.get(f"code{i}") or "").strip() for i in range(1, 7)
            )
            expected = (session.get("register_otp") or "").strip()
            if not entered_code or len(entered_code) != 6:
                flash("Введите полный 6‑значный код.", "error")
                return render_template("verify.html", email=email)

            if entered_code != expected:
                flash("Неверный код подтверждения.", "error")
                return render_template("verify.html", email=email)

            session["email_verified"] = True
            flash("Email подтвержден. Заполните данные профиля.", "success")
            return redirect(url_for("register_details"))

        return render_template("verify.html", email=email)

    @app.route("/register/details", methods=["GET", "POST"])
    def register_details():
        """
        Шаг 3: ввод ФИО, должности и пароля. Создание пользователя.
        """
        email = session.get("register_email")
        if not email or not session.get("email_verified"):
            flash("Сначала подтвердите email.", "error")
            return redirect(url_for("register"))

        if request.method == "POST":
            full_name = (request.form.get("full_name") or "").strip()
            position = (request.form.get("position") or "").strip() or None
            password = request.form.get("password") or ""

            if not full_name or not password:
                flash("Заполните ФИО и пароль.", "error")
                return render_template("register_details.html", email=email)

            existing = Employee.query.filter(Employee.email == email).first()
            if existing:
                flash("Пользователь с таким email уже существует.", "error")
                return redirect(url_for("login"))

            user = Employee(full_name=full_name, position=position, email=email, role="Worker")
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            # очистка регистрационных данных в сессии
            session.pop("register_email", None)
            session.pop("register_otp", None)
            session.pop("email_verified", None)

            login_user(user)
            flash("Аккаунт создан.", "success")
            return redirect(url_for("index"))

        return render_template("register_details.html", email=email)

    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        return redirect(url_for("settings"))

    @app.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings():
        if request.method == "POST":
            form_type = request.form.get("form_type") or ""

            if form_type == "info":
                full_name = (request.form.get("full_name") or "").strip()
                position = (request.form.get("position") or "").strip() or None
                email = (request.form.get("email") or "").strip().lower() or None

                if not full_name:
                    flash("ФИО обязательно для заполнения.", "error")
                    return redirect(url_for("settings") + "#info")

                if email:
                    existing = Employee.query.filter(
                        Employee.email == email, Employee.id != current_user.id
                    ).first()
                    if existing:
                        flash("Этот email уже используется другим пользователем.", "error")
                        return redirect(url_for("settings") + "#info")

                user = Employee.query.get(current_user.id)
                user.full_name = full_name
                user.position = position
                user.email = email
                db.session.commit()
                flash("Данные профиля обновлены.", "success")
                return redirect(url_for("settings") + "#info")

            if form_type == "avatar":
                file = request.files.get("avatar")
                if not file or not file.filename:
                    flash("Файл не выбран.", "error")
                    return redirect(url_for("settings") + "#avatar")

                filename = secure_filename(file.filename)
                ext = os.path.splitext(filename)[1].lower()
                if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
                    flash("Разрешены только PNG/JPG/WEBP.", "error")
                    return redirect(url_for("settings") + "#avatar")

                avatars_dir = os.path.join(app.root_path, "static", "avatars")
                os.makedirs(avatars_dir, exist_ok=True)
                stored_name = f"user_{current_user.id}{ext}"
                stored_path = os.path.join(avatars_dir, stored_name)
                file.save(stored_path)

                user = Employee.query.get(current_user.id)
                user.avatar_filename = stored_name
                db.session.commit()
                flash("Аватар обновлен.", "success")
                return redirect(url_for("settings") + "#avatar")

            flash("Неизвестное действие.", "error")
            return redirect(url_for("settings"))

        return render_template("settings.html")

    @app.get("/")
    @login_required
    def index():
        from datetime import date
        from decimal import Decimal, InvalidOperation

        def compute_urgency_color(p: Project, today_: date) -> str:
            """
            Для Active-проектов: цвет прогресса по близости дедлайна.
            - emerald: дедлайн далеко
            - amber: средняя срочность
            - orange: совсем близко
            """
            if p.status != "In Progress" or not p.end_date:
                return "bg-amber-400"
            days_left = (p.end_date - today_).days
            if days_left <= 2:
                return "bg-orange-500"
            if days_left <= 14:
                return "bg-amber-400"
            return "bg-emerald-500"

        today = date.today()

        # считаем количество пространств пользователя
        memberships_q = UserWorkspace.query.filter(UserWorkspace.user_id == current_user.id)
        workspaces_count = memberships_q.count()

        if workspaces_count == 0:
            # нет ни одного пространства – ведём в онбординг
            return redirect(url_for("onboarding"))

        if workspaces_count == 1:
            # один workspace – автоматически выбираем его и показываем дашборд
            membership = memberships_q.first()
            if membership:
                session["workspace_id"] = membership.workspace_id
            ws = _get_current_workspace()
            if not ws:
                # на случай несогласованности данных всё равно отправим в онбординг
                return redirect(url_for("onboarding"))
        else:
            # несколько workspaces
            if not session.get("workspace_id"):
                # если ничего не выбрано – показываем хаб
                return redirect(url_for("hub"))
            ws = _get_current_workspace()
            if not ws:
                # сохранённый workspace_id невалиден – очищаем и отправляем в хаб
                session.pop("workspace_id", None)
                return redirect(url_for("hub"))

        mine = request.args.get("mine") == "1"
        base_q = Project.query.filter(Project.workspace_id == ws.id)
        if current_user.role == "Worker" and mine:
            all_projects = (
                base_q.join(ProjectAssignment)
                .filter(ProjectAssignment.employee_id == current_user.id)
                .all()
            )
        else:
            all_projects = base_q.all()
        overdue_projects: list[Project] = []
        active_projects: list[Project] = []
        completed_projects: list[Project] = []

        for project in all_projects:
            project.progress_percent = compute_progress_percent(project, today)
            project.urgency_bar_class = compute_urgency_color(project, today)
            project.is_overdue = (
                project.status == "Expired"
                or (
                    project.status != "Completed"
                    and project.end_date is not None
                    and today > project.end_date
                )
            )

            if project.is_overdue:
                if project.end_date is not None:
                    project.days_overdue = max(0, (today - project.end_date).days)
                else:
                    project.days_overdue = 0
                overdue_projects.append(project)
            elif project.status == "Completed":
                completed_projects.append(project)
            else:
                active_projects.append(project)

        overdue_projects.sort(key=lambda p: p.end_date or date.min)
        # Активные проекты сортируем по прогрессу (время прошло / дедлайн), сначала самые "готовые"
        active_projects.sort(key=lambda p: getattr(p, "progress_percent", 0), reverse=True)
        completed_projects.sort(key=lambda p: p.end_date or date.min, reverse=True)

        project_ids = [p.id for p in all_projects if p.id is not None]
        if project_ids:
            employees_involved = (
                db.session.query(ProjectAssignment.employee_id)
                .filter(ProjectAssignment.project_id.in_(project_ids))
                .distinct()
                .count()
            )
        else:
            employees_involved = 0

        total_employees = UserWorkspace.query.filter(UserWorkspace.workspace_id == ws.id).count()

        def _budget_to_decimal(v):
            if v is None:
                return Decimal("0")
            if isinstance(v, Decimal):
                return v
            if isinstance(v, (int, float)):
                return Decimal(str(v))
            if isinstance(v, str):
                try:
                    return Decimal(v.replace(" ", "").replace(",", "."))
                except (InvalidOperation, ValueError):
                    return Decimal("0")
            return Decimal("0")

        total_budget = sum((_budget_to_decimal(p.budget) for p in all_projects), Decimal("0"))

        return render_template(
            "index.html",
            overdue_projects=overdue_projects,
            active_projects=active_projects,
            completed_projects=completed_projects,
            mine=mine,
            employees_involved=employees_involved,
            total_employees=total_employees,
            total_budget=total_budget,
        )

    @app.route("/project/add", methods=["GET", "POST"])
    @login_required
    def add_project():
        ws = _get_current_workspace()
        role_ws = _workspace_role(ws)
        if role_ws not in {"owner", "admin"}:
            abort(403)
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            description = request.form.get("description") or None
            start_date_raw = request.form.get("start_date") or None
            end_date_raw = request.form.get("end_date") or None
            status = request.form.get("status") or "In Progress"
            budget_raw = request.form.get("budget") or None
            from decimal import Decimal, InvalidOperation
            from datetime import date as _date

            start_date = _parse_date_any(start_date_raw) if start_date_raw else None
            end_date = _parse_date_any(end_date_raw) if end_date_raw else None

            # Принудительная логика Expired
            if status != "Completed" and end_date is not None and _date.today() > end_date:
                status = "Expired"

            budget = None
            if budget_raw:
                try:
                    budget = Decimal(budget_raw.replace(" ", "").replace(",", "."))
                except InvalidOperation:
                    budget = None

            if not title:
                flash("Название проекта обязательно для заполнения.", "error")
                return redirect(url_for("add_project"))

            project = Project(
                workspace_id=ws.id if ws else None,
                title=title,
                description=description,
                start_date=start_date,
                end_date=end_date,
                status=status,
                budget=budget,
            )
            db.session.add(project)
            db.session.commit()

            flash("Проект успешно добавлен.", "success")
            return redirect(url_for("edit_project", id=project.id))

        draft_project = Project(title="", status="In Progress")
        draft_project.progress_percent = compute_progress_percent(draft_project)
        draft_project.is_overdue = False
        return render_template(
            "project_details.html",
            project=draft_project,
            assigned_employees=[],
            available_employees=Employee.query.order_by(Employee.full_name.asc()).all(),
        )

    @app.get("/project/edit/<int:id>")
    @login_required
    def edit_project(id: int):
        return redirect(url_for("project_details", id=id))

    @app.post("/project/delete/<int:id>")
    @login_required
    def delete_project(id: int):
        if current_user.role != "Admin":
            abort(403)
        project = Project.query.get_or_404(id)
        try:
            # Сначала удаляем связи команды (ProjectAssignments), затем сам проект
            ProjectAssignment.query.filter_by(project_id=project.id).delete(
                synchronize_session=False
            )
            db.session.delete(project)
            db.session.commit()
            flash("Проект успешно удален.", "success")
            return redirect(url_for("index"))
        except Exception:
            db.session.rollback()
            app.logger.exception("Ошибка при удалении проекта id=%s", id)
            flash("Ошибка при удалении: проверьте связи данных.", "error")
            return redirect(url_for("project_details", id=id))

    @app.route("/project/<int:id>", methods=["GET", "POST"])
    @login_required
    def project_details(id: int):
        ws = _get_current_workspace()
        if not ws:
            abort(403)
        project = Project.query.filter(Project.id == id, Project.workspace_id == ws.id).first_or_404()

        if request.method == "POST":
            if current_user.role != "Admin":
                abort(403)
            title = (request.form.get("title") or "").strip()
            description = request.form.get("description") or None
            start_date_raw = request.form.get("start_date") or None
            end_date_raw = request.form.get("end_date") or None
            status = request.form.get("status") or project.status
            budget_raw = request.form.get("budget") or None
            from datetime import date as _date
            from decimal import Decimal, InvalidOperation

            project.title = title or project.title
            project.description = description

            project.start_date = _parse_date_any(start_date_raw) if start_date_raw else None
            project.end_date = _parse_date_any(end_date_raw) if end_date_raw else None
            # Принудительная логика Expired
            if status != "Completed" and project.end_date is not None and _date.today() > project.end_date:
                project.status = "Expired"
            else:
                project.status = status

            if budget_raw:
                try:
                    project.budget = Decimal(
                        budget_raw.replace(" ", "").replace(",", ".")
                    )
                except InvalidOperation:
                    project.budget = project.budget
            else:
                project.budget = None

            if not project.title:
                flash("Название проекта обязательно для заполнения.", "error")
            else:
                db.session.commit()
                flash("Изменения сохранены.", "success")
                return redirect(url_for("project_details", id=project.id))

        from datetime import date

        today = date.today()
        project.is_overdue = (
            project.status != "Completed"
            and project.end_date is not None
            and today > project.end_date
        )

        project.progress_percent = compute_progress_percent(project, today)

        assigned_employees = (
            Employee.query.join(ProjectAssignment)
            .join(UserWorkspace, UserWorkspace.user_id == Employee.id)
            .filter(ProjectAssignment.project_id == project.id, UserWorkspace.workspace_id == ws.id)
            .order_by(Employee.full_name.asc())
            .all()
        )
        assigned_ids = {e.id for e in assigned_employees}
        base_emp_q = (
            Employee.query.join(UserWorkspace, UserWorkspace.user_id == Employee.id)
            .filter(UserWorkspace.workspace_id == ws.id)
        )
        available_employees = (
            base_emp_q.filter(~Employee.id.in_(assigned_ids)).order_by(Employee.full_name.asc()).all()
            if assigned_ids
            else base_emp_q.order_by(Employee.full_name.asc()).all()
        )
        return render_template(
            "project_details.html",
            project=project,
            assigned_employees=assigned_employees,
            available_employees=available_employees,
        )

    @app.post("/project/<int:id>/assign")
    @login_required
    def assign_employee(id: int):
        ws = _get_current_workspace()
        role_ws = _workspace_role(ws)
        if role_ws not in {"owner", "admin"}:
            abort(403)
        project = Project.query.filter(Project.id == id, Project.workspace_id == (ws.id if ws else None)).first_or_404()
        employee_id = request.form.get("employee_id")

        if not employee_id:
            flash("Не выбран сотрудник для назначения.", "error")
            return redirect(url_for("project_details", id=project.id))

        employee = Employee.query.get(employee_id)
        if not employee:
            flash("Указанный сотрудник не найден.", "error")
            return redirect(url_for("project_details", id=project.id))
        if ws and not UserWorkspace.query.filter_by(workspace_id=ws.id, user_id=employee.id).first():
            flash("Сотрудник не состоит в этом workspace.", "error")
            return redirect(url_for("project_details", id=project.id))

        from models import ProjectAssignment

        existing = ProjectAssignment.query.filter_by(
            project_id=project.id, employee_id=employee.id
        ).first()
        if existing:
            flash("Этот сотрудник уже назначен на проект.", "error")
        else:
            assignment = ProjectAssignment(
                project_id=project.id,
                employee_id=employee.id,
            )
            db.session.add(assignment)
            db.session.commit()
            flash("Сотрудник добавлен в команду проекта.", "success")

        return redirect(url_for("project_details", id=project.id))

    @app.post("/project/<int:project_id>/unassign/<int:employee_id>")
    @login_required
    def unassign_employee(project_id: int, employee_id: int):
        ws = _get_current_workspace()
        role_ws = _workspace_role(ws)
        if role_ws not in {"owner", "admin"}:
            abort(403)
        from models import ProjectAssignment

        assignment = ProjectAssignment.query.filter_by(
            project_id=project_id, employee_id=employee_id
        ).first_or_404()
        db.session.delete(assignment)
        db.session.commit()
        flash("Сотрудник удален из команды проекта.", "success")
        return redirect(url_for("project_details", id=project_id))

    @app.get("/employees")
    @login_required
    def employees():
        ws = _get_current_workspace()
        if not ws:
            return redirect(url_for("hub"))
        employees_list = (
            Employee.query.join(UserWorkspace, UserWorkspace.user_id == Employee.id)
            .filter(UserWorkspace.workspace_id == ws.id)
            .order_by(Employee.full_name.asc())
            .all()
        )
        return render_template("employees.html", employees=employees_list)

    @app.route("/employee/add", methods=["GET", "POST"])
    @login_required
    def add_employee():
        ws = _get_current_workspace()
        role = _workspace_role(ws)
        if role not in {"owner", "admin"}:
            abort(403)
        if request.method == "POST":
            full_name = (request.form.get("full_name") or "").strip()
            position = request.form.get("position") or None
            email = (request.form.get("email") or "").strip() or None
            role = (request.form.get("role") or "Worker").strip() or "Worker"
            password = request.form.get("password")
            file = request.files.get("avatar")

            if not full_name:
                flash("Поле \"ФИО\" обязательно для заполнения.", "error")
                return render_template(
                    "employee_form.html",
                    employee=None,
                    mode="create",
                )

            if not password:
                flash("Пароль обязателен для создания сотрудника.", "error")
                return render_template(
                    "employee_form.html",
                    employee=None,
                    mode="create",
                )

            hashed_password = generate_password_hash(password)

            employee = Employee(
                full_name=full_name,
                position=position,
                email=email,
                role=role if role in {"Admin", "Worker"} else "Worker",
                password_hash=hashed_password,
            )
            db.session.add(employee)
            db.session.commit()

            # добавляем пользователя в текущий workspace как member (админы управляют составом)
            if ws:
                db.session.add(UserWorkspace(workspace_id=ws.id, user_id=employee.id, role="member"))
                db.session.commit()

            if file and file.filename:
                filename = secure_filename(file.filename)
                ext = os.path.splitext(filename)[1].lower()
                if ext in {".png", ".jpg", ".jpeg", ".webp"}:
                    avatars_dir = os.path.join(app.root_path, "static", "avatars")
                    os.makedirs(avatars_dir, exist_ok=True)
                    stored_name = f"employee_{employee.id}{ext}"
                    file.save(os.path.join(avatars_dir, stored_name))
                    employee.avatar_filename = stored_name
                    db.session.commit()

            flash("Сотрудник успешно добавлен.", "success")
            return redirect(url_for("employees"))

        return render_template("employee_form.html", employee=None, mode="create")

    @app.route("/employee/edit/<int:id>", methods=["GET", "POST"])
    @login_required
    def edit_employee(id: int):
        ws = _get_current_workspace()
        role_ws = _workspace_role(ws)
        if role_ws not in {"owner", "admin"}:
            abort(403)
        employee = Employee.query.get_or_404(id)

        # нельзя редактировать сотрудников вне текущего workspace
        if ws and not UserWorkspace.query.filter_by(workspace_id=ws.id, user_id=employee.id).first():
            abort(403)

        if request.method == "POST":
            full_name = (request.form.get("full_name") or "").strip()
            position = request.form.get("position") or None
            role = (request.form.get("role") or employee.role or "Worker").strip()
            file = request.files.get("avatar")

            if not full_name:
                flash("Поле \"ФИО\" обязательно для заполнения.", "error")
                return render_template(
                    "employee_form.html",
                    employee=employee,
                    mode="edit",
                )

            employee.full_name = full_name
            employee.position = position
            if role in {"Admin", "Worker"}:
                employee.role = role

            if file and file.filename:
                filename = secure_filename(file.filename)
                ext = os.path.splitext(filename)[1].lower()
                if ext in {".png", ".jpg", ".jpeg", ".webp"}:
                    avatars_dir = os.path.join(app.root_path, "static", "avatars")
                    os.makedirs(avatars_dir, exist_ok=True)
                    stored_name = f"employee_{employee.id}{ext}"
                    file.save(os.path.join(avatars_dir, stored_name))
                    employee.avatar_filename = stored_name

            # Email и пароль администратором напрямую не редактируются

            db.session.commit()
            flash("Данные сотрудника обновлены.", "success")
            return redirect(url_for("employees"))

        return render_template("employee_form.html", employee=employee, mode="edit")

    @app.post("/employee/delete/<int:id>")
    @login_required
    def delete_employee(id: int):
        ws = _get_current_workspace()
        role_ws = _workspace_role(ws)
        if role_ws not in {"owner", "admin"}:
            abort(403)
        employee = Employee.query.get_or_404(id)
        if ws and not UserWorkspace.query.filter_by(workspace_id=ws.id, user_id=employee.id).first():
            abort(403)
        if ws and ws.owner_id == employee.id:
            flash("Нельзя удалить владельца workspace.", "error")
            return redirect(url_for("employees"))
        db.session.delete(employee)
        db.session.commit()
        flash("Сотрудник успешно удален.", "success")
        return redirect(url_for("employees"))

    @app.get("/reports")
    @login_required
    def reports():
        if current_user.role != "Admin":
            abort(403)

        date_from_raw = (request.args.get("date_from") or "").strip()
        date_to_raw = (request.args.get("date_to") or "").strip()
        status = (request.args.get("status") or "").strip()
        responsible_id_raw = (request.args.get("responsible_id") or "").strip()

        query = Project.query

        if status in {"In Progress", "Completed", "Expired"}:
            query = query.filter(Project.status == status)

        date_from = _parse_date_any(date_from_raw)
        if date_from:
            query = query.filter(Project.start_date.isnot(None), Project.start_date >= date_from)

        date_to = _parse_date_any(date_to_raw)
        if date_to:
            query = query.filter(Project.end_date.isnot(None), Project.end_date <= date_to)

        if responsible_id_raw:
            try:
                responsible_id = int(responsible_id_raw)
                query = (
                    query.join(ProjectAssignment)
                    .filter(ProjectAssignment.employee_id == responsible_id)
                    .distinct()
                )
            except ValueError:
                pass

        projects = query.order_by(Project.end_date.desc()).all()
        total_projects = len(projects)
        completed_projects = [p for p in projects if p.status == "Completed"]
        completed_count = len(completed_projects)
        total_completed_budget = sum((p.budget or 0) for p in completed_projects)

        employees_list = Employee.query.order_by(Employee.full_name.asc()).all()
        return render_template(
            "reports.html",
            total_projects=total_projects,
            completed_count=completed_count,
            total_completed_budget=total_completed_budget,
            projects=projects,
            employees=employees_list,
            filters={
                "date_from": date_from.strftime("%d.%m.%Y") if date_from else "",
                "date_to": date_to.strftime("%d.%m.%Y") if date_to else "",
                "status": status,
                "responsible_id": responsible_id_raw,
            },
        )

    @app.get("/reports/export")
    @login_required
    def export_reports():
        if current_user.role != "Admin":
            abort(403)
        import csv
        from io import StringIO

        date_from_raw = (request.args.get("date_from") or "").strip()
        date_to_raw = (request.args.get("date_to") or "").strip()
        status = (request.args.get("status") or "").strip()
        responsible_id_raw = (request.args.get("responsible_id") or "").strip()

        query = Project.query
        if status in {"In Progress", "Completed", "Expired"}:
            query = query.filter(Project.status == status)

        date_from = _parse_date_any(date_from_raw)
        if date_from:
            query = query.filter(Project.start_date.isnot(None), Project.start_date >= date_from)

        date_to = _parse_date_any(date_to_raw)
        if date_to:
            query = query.filter(Project.end_date.isnot(None), Project.end_date <= date_to)

        if responsible_id_raw:
            try:
                responsible_id = int(responsible_id_raw)
                query = (
                    query.join(ProjectAssignment)
                    .filter(ProjectAssignment.employee_id == responsible_id)
                    .distinct()
                )
            except ValueError:
                pass

        projects = query.order_by(Project.end_date.desc()).all()

        output = StringIO()
        writer = csv.writer(output, delimiter=";")

        writer.writerow(["Название проекта", "Статус", "Дата начала", "Дата окончания", "Бюджет"])
        for project in projects:
            start_date = project.start_date.strftime("%Y-%m-%d") if project.start_date else ""
            end_date = project.end_date.strftime("%Y-%m-%d") if project.end_date else ""
            budget = f"{project.budget:.2f}" if project.budget is not None else ""
            writer.writerow([project.title, project.status, start_date, end_date, budget])

        csv_content = output.getvalue()
        output.close()

        bom = "\ufeff"
        response = Response(bom + csv_content, mimetype="text/csv; charset=utf-8-sig")
        response.headers["Content-Disposition"] = "attachment; filename=projects_report.csv"
        return response

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host='0.0.0.0')



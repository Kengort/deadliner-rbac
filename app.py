from flask import Flask, render_template, request, redirect, url_for, flash, Response, abort
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

from config import Config
from models import db, Project, Employee, ProjectAssignment


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


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str):
        try:
            return db.session.get(Employee, int(user_id))
        except (TypeError, ValueError):
            return None

    with app.app_context():
        ensure_database_and_tables()

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
            return redirect(url_for("index"))

        return render_template("login.html")

    @app.get("/logout")
    @login_required
    def logout():
        logout_user()
        flash("Вы вышли из системы.", "success")
        return redirect(url_for("login"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            full_name = (request.form.get("full_name") or "").strip()
            position = (request.form.get("position") or "").strip() or None
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""

            if not full_name or not email or not password:
                flash("Заполните ФИО, email и пароль.", "error")
                return render_template("register.html")

            existing = Employee.query.filter(Employee.email == email).first()
            if existing:
                flash("Пользователь с таким email уже существует.", "error")
                return render_template("register.html")

            user = Employee(full_name=full_name, position=position, email=email, role="Worker")
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            login_user(user)
            flash("Аккаунт создан.", "success")
            return redirect(url_for("index"))

        return render_template("register.html")

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

        def compute_progress_percent(p: Project, today_: date) -> int:
            if p.status == "Completed":
                return 100
            if p.status == "Expired":
                return 95
            if p.status == "In Progress" and p.start_date and p.end_date:
                total_days = (p.end_date - p.start_date).days
                if total_days > 0:
                    elapsed_days = (today_ - p.start_date).days
                    ratio = elapsed_days / total_days
                    return max(0, min(95, int(ratio * 100)))
            return 10

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

        mine = request.args.get("mine") == "1"
        if current_user.role == "Worker" and mine:
            all_projects = (
                Project.query.join(ProjectAssignment)
                .filter(ProjectAssignment.employee_id == current_user.id)
                .all()
            )
        else:
            all_projects = Project.query.all()
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
                overdue_projects.append(project)
            elif project.status == "Completed":
                completed_projects.append(project)
            else:
                active_projects.append(project)

        overdue_projects.sort(key=lambda p: p.end_date or date.min)
        active_projects.sort(key=lambda p: p.end_date or date.max)
        completed_projects.sort(key=lambda p: p.end_date or date.min, reverse=True)

        return render_template(
            "index.html",
            overdue_projects=overdue_projects,
            active_projects=active_projects,
            completed_projects=completed_projects,
            mine=mine,
        )

    @app.route("/project/add", methods=["GET", "POST"])
    @login_required
    def add_project():
        if current_user.role != "Admin":
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
        draft_project.progress_percent = 10
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
        project = Project.query.get_or_404(id)

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

        progress_percent = 10
        if project.status == "Completed":
            progress_percent = 100
        elif project.status == "In Progress" and project.start_date and project.end_date:
            total_days = (project.end_date - project.start_date).days
            if total_days > 0:
                elapsed_days = (today - project.start_date).days
                ratio = elapsed_days / total_days
                progress_percent = max(0, min(95, int(ratio * 100)))
        project.progress_percent = progress_percent

        assigned_employees = (
            Employee.query.join(ProjectAssignment)
            .filter(ProjectAssignment.project_id == project.id)
            .order_by(Employee.full_name.asc())
            .all()
        )
        assigned_ids = {e.id for e in assigned_employees}
        available_employees = (
            Employee.query.filter(~Employee.id.in_(assigned_ids)).order_by(Employee.full_name.asc()).all()
            if assigned_ids
            else Employee.query.order_by(Employee.full_name.asc()).all()
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
        if current_user.role != "Admin":
            abort(403)
        project = Project.query.get_or_404(id)
        employee_id = request.form.get("employee_id")

        if not employee_id:
            flash("Не выбран сотрудник для назначения.", "error")
            return redirect(url_for("project_details", id=project.id))

        employee = Employee.query.get(employee_id)
        if not employee:
            flash("Указанный сотрудник не найден.", "error")
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
        if current_user.role != "Admin":
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
        employees_list = Employee.query.order_by(Employee.full_name.asc()).all()
        return render_template("employees.html", employees=employees_list)

    @app.route("/employee/add", methods=["GET", "POST"])
    @login_required
    def add_employee():
        if current_user.role != "Admin":
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
        if current_user.role != "Admin":
            abort(403)
        employee = Employee.query.get_or_404(id)

        if request.method == "POST":
            full_name = (request.form.get("full_name") or "").strip()
            position = request.form.get("position") or None
            email = (request.form.get("email") or "").strip() or None
            role = (request.form.get("role") or employee.role or "Worker").strip()
            password = request.form.get("password") or ""
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
            employee.email = email
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

            if password.strip():
                employee.password_hash = generate_password_hash(password.strip())

            db.session.commit()
            flash("Данные сотрудника обновлены.", "success")
            return redirect(url_for("employees"))

        return render_template("employee_form.html", employee=employee, mode="edit")

    @app.post("/employee/delete/<int:id>")
    @login_required
    def delete_employee(id: int):
        if current_user.role != "Admin":
            abort(403)
        employee = Employee.query.get_or_404(id)
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
    app.run(debug=True)



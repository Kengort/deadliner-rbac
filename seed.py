from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from werkzeug.security import generate_password_hash

from app import create_app, _generate_invite_code  # type: ignore[attr-defined]
from models import db, Employee, Project, ProjectAssignment, Workspace, UserWorkspace

try:
    # Опциональная модель задач, если она существует в проекте
    from models import Task  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - Task может отсутствовать
    Task = None  # type: ignore


def _reset_database() -> None:
    """
    Полная очистка БД с учетом внешних ключей.
    Порядок удаления:
    1) ProjectAssignment
    2) UserWorkspace
    3) Task (если есть)
    4) Project
    5) Workspace
    6) Employee
    """
    ProjectAssignment.query.delete()
    UserWorkspace.query.delete()
    if Task is not None:
        Task.query.delete()  # type: ignore[attr-defined]
    Project.query.delete()
    Workspace.query.delete()
    Employee.query.delete()
    db.session.commit()


def _seed_users() -> list[Employee]:
    """Создаёт 12+ пользователей с русскими ФИО и английскими email."""
    users_def = [
        ("Савик Никита", "Chief Executive Officer", "ceo@flux.dev", "Admin"),
        ("Иван Петров", "Lead Backend Engineer", "dev.lead@flux.dev", "Admin"),
        ("Анна Смирнова", "Product Manager", "pm.pro@flux.dev", "Admin"),
        ("Мария Кузнецова", "UI/UX Designer", "designer@flux.dev", "Worker"),
        ("Алексей Орлов", "DevOps Engineer", "devops@flux.dev", "Worker"),
        ("Дмитрий Иванов", "QA Engineer", "qa.engineer@flux.dev", "Worker"),
        ("Екатерина Лебедева", "Frontend Developer", "frontend.dev@flux.dev", "Worker"),
        ("Сергей Волков", "Backend Developer", "backend.dev@flux.dev", "Worker"),
        ("Ольга Морозова", "Business Analyst", "ba.analytics@flux.dev", "Worker"),
        ("Павел Николаев", "iOS Developer", "ios.dev@flux.dev", "Worker"),
        ("Ирина Алексеева", "Android Developer", "android.dev@flux.dev", "Worker"),
        ("Артем Сидоров", "Support Engineer", "support@flux.dev", "Worker"),
    ]

    employees: list[Employee] = []
    for full_name, position, email, role in users_def:
        emp = Employee(
            full_name=full_name,
            position=position,
            email=email,
            role=role,
            password_hash=generate_password_hash("password123"),
        )
        db.session.add(emp)
        employees.append(emp)

    db.session.commit()
    return employees


def _seed_workspace_and_members(employees: list[Employee]) -> Workspace:
    """
    Создаёт один основной workspace 'Flux Workspace' и
    назначает владельцем пользователя 'Савик Никита' (если есть).
    """
    owner = next((u for u in employees if u.full_name == "Савик Никита"), None)
    if owner is None:
        owner = next((u for u in employees if u.role == "Admin"), None)
    if owner is None:
        owner = employees[0]

    invite = _generate_invite_code()
    ws = Workspace(name="Flux Workspace", invite_code=invite, owner_id=owner.id)
    db.session.add(ws)
    db.session.flush()

    for emp in employees:
        role = "owner" if emp.id == owner.id else "member"
        db.session.add(UserWorkspace(workspace_id=ws.id, user_id=emp.id, role=role))

    db.session.commit()
    return ws


def _seed_projects(ws: Workspace) -> list[Project]:
    """Создаёт 15+ проектов с разными статусами и дедлайнами."""
    titles = [
        "Система мониторинга и алертинга",
        "Переезд CI/CD в GitHub Actions",
        "Интеграция платежного шлюза",
        "Мобильное приложение v2",
        "Рефакторинг legacy-кода",
        "Автоматизация тестирования",
        "Портал для корпоративных клиентов",
        "Модуль аналитики и отчетности",
        "Платформа уведомлений (Email/SMS/Push)",
        "Сервис авторизации и SSO",
        "Миграция БД на кластер SQL Server",
        "Оптимизация производительности API",
        "Dashboard для топ-менеджмента",
        "Внутренний портал знаний",
        "Система управления инцидентами",
    ]

    rng = random.Random(42)
    today = date.today()

    def rand_budget() -> Decimal:
        # 500 000 – 5 000 000 BYN
        return Decimal(str(rng.randint(500_000, 5_000_000)))

    projects: list[Project] = []

    for idx, title in enumerate(titles):
        # 3–4 сильно просроченных
        if idx < 4:
            end = date(2026, 2, 10) - timedelta(days=(4 - idx) * 7)
            start = end - timedelta(days=rng.randint(60, 140))
            status = "Expired"
        # 4 завершённых
        elif idx < 8:
            end = today - timedelta(days=rng.randint(10, 90))
            start = end - timedelta(days=rng.randint(60, 180))
            status = "Completed"
        # остальные в работе
        else:
            start = today - timedelta(days=rng.randint(0, 60))
            end = today + timedelta(days=rng.randint(14, 160))
            status = "In Progress"

        proj = Project(
            workspace_id=ws.id,
            title=title,
            description="Проект для демо‑данных Flux.",
            start_date=start,
            end_date=end,
            status=status,
            budget=rand_budget(),
        )
        db.session.add(proj)
        projects.append(proj)

    db.session.commit()
    return projects


def _seed_assignments(projects: list[Project], employees: list[Employee]) -> None:
    """Назначает на каждый проект по 3–5 реальных участников."""
    rng = random.Random(123)
    assignments: list[ProjectAssignment] = []

    for proj in projects:
        team_size = rng.randint(3, 5)
        team = rng.sample(employees, k=min(team_size, len(employees)))
        for emp in team:
            assignments.append(ProjectAssignment(project_id=proj.id, employee_id=emp.id))

    db.session.add_all(assignments)
    db.session.commit()


def run_seed() -> None:
    app = create_app()

    with app.app_context():
        _reset_database()

        employees = _seed_users()
        workspace = _seed_workspace_and_members(employees)
        projects = _seed_projects(workspace)
        _seed_assignments(projects, employees)

        print(
            f"Seed completed: {len(employees)} users, "
            f"{len(projects)} projects in workspace '{workspace.name}'."
        )


if __name__ == "__main__":
    run_seed()


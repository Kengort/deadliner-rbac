import random
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from app import create_app
from models import db, Project, Employee, ProjectAssignment, Workspace, UserWorkspace


def reset_database():
    """Полная очистка пользовательских и проектных таблиц."""
    # Удаляем связи команда–проект
    ProjectAssignment.query.delete()
    db.session.commit()

    # Удаляем связи пользователь–workspace
    UserWorkspace.query.delete()
    db.session.commit()

    # Удаляем проекты и workspaces
    Project.query.delete()
    Workspace.query.delete()
    db.session.commit()

    # Удаляем пользователей
    Employee.query.delete()
    db.session.commit()


def seed():
    """Заполняет БД масштабными реалистичными данными."""
    reset_database()

    # --- Пользователи ---
    users_def = [
        ("Савик Никита", "CEO", "ceo@flux.dev", "Admin"),
        ("Иван Петров", "Lead Backend Engineer", "lead_backend@flux.dev", "Admin"),
        ("Анна Смирнова", "Lead Frontend Engineer", "lead_frontend@flux.dev", "Admin"),
        ("Мария Кузнецова", "UI/UX Designer", "designer_1@flux.dev", "Worker"),
        ("Алексей Орлов", "Product Manager", "pm_flux@flux.dev", "Worker"),
        ("Дмитрий Иванов", "QA Engineer", "qa_engineer@flux.dev", "Worker"),
        ("Екатерина Лебедева", "DevOps Engineer", "devops@flux.dev", "Worker"),
        ("Сергей Волков", "Data Engineer", "data_engineer@flux.dev", "Worker"),
        ("Ольга Морозова", "Business Analyst", "ba_analytics@flux.dev", "Worker"),
        ("Павел Николаев", "iOS Developer", "ios_dev@flux.dev", "Worker"),
        ("Ирина Алексеева", "Android Developer", "android_dev@flux.dev", "Worker"),
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

    # Определяем владельца workspace – пользователь с именем "Савик Никита", если есть,
    # иначе первый Admin, иначе первый пользователь.
    owner = next((u for u in employees if u.full_name == "Савик Никита"), None)
    if owner is None:
        owner = next((u for u in employees if u.role == "Admin"), None)
    if owner is None:
        owner = employees[0]

    # --- Workspace ---
    from app import _generate_invite_code  # type: ignore[attr-defined]

    invite_code = _generate_invite_code()
    ws = Workspace(name="Flux Workspace", invite_code=invite_code, owner_id=owner.id)
    db.session.add(ws)
    db.session.flush()

    # Владельца записываем в UserWorkspace как owner, остальных – как member
    for emp in employees:
        role = "owner" if emp.id == owner.id else "member"
        db.session.add(UserWorkspace(workspace_id=ws.id, user_id=emp.id, role=role))

    db.session.commit()

    # --- Проекты ---
    project_titles = [
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

    # Бюджеты BYN
    budgets = [
        500_000,
        750_000,
        900_000,
        1_200_000,
        1_500_000,
        1_800_000,
        2_000_000,
        2_400_000,
        2_800_000,
        3_000_000,
        3_500_000,
        4_000_000,
        4_500_000,
        4_800_000,
        5_000_000,
    ]

    today = date.today()
    projects: list[Project] = []

    for idx, title in enumerate(project_titles):
        # Жёстко просроченные первые 3 проекта (январь–февраль 2026)
        if idx < 3:
            end = date(2026, 2, 10) - timedelta(days=(3 - idx) * 5)
            start = end - timedelta(days=90)
            status = "Expired"
        # Завершённые последние 4 проекта
        elif idx >= len(project_titles) - 4:
            end = today - timedelta(days=random.randint(5, 60))
            start = end - timedelta(days=random.randint(60, 150))
            status = "Completed"
        # Остальные – в работе
        else:
            start = today - timedelta(days=random.randint(5, 60))
            end = today + timedelta(days=random.randint(10, 120))
            status = "In Progress"

        budget_value = random.choice(budgets)

        proj = Project(
            workspace_id=ws.id,
            title=title,
            description=None,
            start_date=start,
            end_date=end,
            status=status,
            budget=budget_value,
        )
        db.session.add(proj)
        projects.append(proj)

    db.session.commit()

    # --- Связи проект–сотрудники ---
    for proj in projects:
        # для каждого проекта 3–7 участников
        team_size = random.randint(3, 7)
        team_members = random.sample(employees, k=min(team_size, len(employees)))
        for member in team_members:
            db.session.add(
                ProjectAssignment(
                    project_id=proj.id,
                    employee_id=member.id,
                )
            )

    db.session.commit()


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed()
        print("Database has been reset and seeded with demo data.")


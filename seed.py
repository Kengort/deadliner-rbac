from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from app import create_app
from models import db, Employee, Project, ProjectAssignment


def run_seed() -> None:
    app = create_app()

    with app.app_context():
        # Очистка данных в правильном порядке с учетом внешних ключей
        ProjectAssignment.query.delete()
        Employee.query.delete()
        Project.query.delete()
        db.session.commit()

        rng = random.Random(42)

        first_names = [
            "Иван", "Петр", "Алексей", "Дмитрий", "Сергей", "Никита", "Андрей", "Евгений", "Максим", "Кирилл",
            "Анна", "Мария", "Ольга", "Екатерина", "Алина", "Дарья", "Виктория", "Наталья", "Юлия", "Татьяна",
        ]
        last_names = [
            "Иванов", "Петров", "Сидоров", "Кузнецов", "Смирнов", "Попов", "Лебедев", "Козлов", "Новиков", "Морозов",
            "Волков", "Федоров", "Михайлов", "Павлов", "Семенов", "Егоров", "Николаев", "Алексеев", "Зайцев", "Соловьев",
        ]
        patronymics = [
            "Иванович", "Петрович", "Алексеевич", "Дмитриевич", "Сергеевич",
            "Ивановна", "Петровна", "Алексеевна", "Дмитриевна", "Сергеевна",
        ]

        roles = [
            "Android Developer",
            "iOS Developer",
            "Backend Developer",
            "Frontend Developer",
            "QA Engineer",
            "DevOps Engineer",
            "Data Scientist",
            "Business Analyst",
            "System Architect",
            "Product Manager",
            "Project Manager",
            "UI/UX Designer",
            "Security Engineer",
            "SRE Engineer",
            "ML Engineer",
            "DBA",
            "Support Engineer",
            "Technical Writer",
            "Mobile QA Engineer",
            "Fullstack Developer",
        ]

        def gen_full_name(i: int) -> str:
            fn = rng.choice(first_names)
            ln = rng.choice(last_names)
            pt = rng.choice(patronymics)
            return f"{ln} {fn} {pt}"

        employees: list[Employee] = []
        used_emails: set[str] = set()
        for i in range(40):
            full_name = gen_full_name(i)
            position = rng.choice(roles)
            base = f"{full_name.split()[0].lower()}.{full_name.split()[1].lower()}{i+1}"
            email = f"{base}@company.local"
            while email in used_emails:
                email = f"{base}{rng.randint(10,99)}@company.local"
            used_emails.add(email)
            employees.append(Employee(full_name=full_name, position=position, email=email))

        db.session.add_all(employees)
        db.session.commit()

        # Глобальный админ
        admin = Employee(
            full_name="Global Admin",
            position="Administrator",
            email="admin@company.local",
            role="Admin",
        )
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()

        # Пароли/роли для остальных сотрудников
        # 90% Worker, 10% Admin
        rng.shuffle(employees)
        admin_count = max(1, int(len(employees) * 0.10))
        admin_ids = {e.id for e in employees[:admin_count]}
        for e in employees:
            e.set_password("password123")
            e.role = "Admin" if e.id in admin_ids else "Worker"
        db.session.commit()

        today = date.today()

        titles = [
            "Интеграция платежного шлюза",
            "Переезд CI/CD в GitHub Actions",
            "Система мониторинга и алертинга",
            "Редизайн интерфейса кабинета",
            "Платформа аналитики продаж",
            "Миграция базы данных",
            "Оптимизация производительности API",
            "Мобильное приложение v2",
            "Единый каталог услуг",
            "Интеграция с CRM",
            "Портал для партнеров",
            "Автоматизация тестирования",
            "Внедрение SSO",
            "Управление доступами и ролями",
            "Переход на микросервисы",
        ]

        # Требования по распределению:
        # 3 просрочены (In Progress, end_date < today)
        # 4 завершены (Completed)
        # остальные в работе (In Progress, end_date >= today)
        projects: list[Project] = []

        def budget() -> Decimal:
            return Decimal(str(rng.randint(500_000, 5_000_000)))  # BYN

        # просроченные (Expired)
        for i in range(3):
            end_date = today - timedelta(days=rng.randint(5, 60))
            start_date = end_date - timedelta(days=rng.randint(30, 120))
            projects.append(
                Project(
                    title=titles[i],
                    description="Автоматически сгенерированный проект для демо-данных.",
                    start_date=start_date,
                    end_date=end_date,
                    status="Expired",
                    budget=budget(),
                )
            )

        # завершенные
        for i in range(3, 7):
            end_date = today - timedelta(days=rng.randint(1, 120))
            start_date = end_date - timedelta(days=rng.randint(20, 140))
            projects.append(
                Project(
                    title=titles[i],
                    description="Завершенный проект для проверки отчетов.",
                    start_date=start_date,
                    end_date=end_date,
                    status="Completed",
                    budget=budget(),
                )
            )

        # активные
        for i in range(7, 15):
            start_date = today - timedelta(days=rng.randint(0, 40))
            end_date = today + timedelta(days=rng.randint(3, 120))
            projects.append(
                Project(
                    title=titles[i],
                    description="Проект в работе с различными сроками и бюджетом.",
                    start_date=start_date,
                    end_date=end_date,
                    status="In Progress",
                    budget=budget(),
                )
            )

        db.session.add_all(projects)
        db.session.commit()

        # Автоперевод в Expired по дедлайну (если не Completed)
        for p in projects:
            if p.status != "Completed" and p.end_date is not None and p.end_date < today:
                p.status = "Expired"
        db.session.commit()

        # Назначения: 2..6 сотрудников на проект, один сотрудник может быть в 2 проектах
        # Гарантируем, что у каждого сотрудника максимум 2 назначения.
        employee_slots: dict[int, int] = {e.id: 2 for e in employees}
        assignments: list[ProjectAssignment] = []

        for p in projects:
            team_size = rng.randint(2, 6)
            eligible = [eid for eid, slots in employee_slots.items() if slots > 0]
            rng.shuffle(eligible)
            chosen = eligible[:team_size]
            for eid in chosen:
                assignments.append(ProjectAssignment(project_id=p.id, employee_id=eid))
                employee_slots[eid] -= 1

        db.session.add_all(assignments)
        db.session.commit()

        print("Seed: 40 сотрудников, 15 проектов, назначения созданы.")


if __name__ == "__main__":
    run_seed()


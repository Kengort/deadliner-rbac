from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class Project(db.Model):
    __tablename__ = "Projects"

    id = db.Column("ID", db.Integer, primary_key=True)
    title = db.Column("Title", db.NVARCHAR(255), nullable=False)
    description = db.Column("Description", db.NVARCHAR(None))
    start_date = db.Column("StartDate", db.Date, nullable=True)
    end_date = db.Column("EndDate", db.Date, nullable=True)
    status = db.Column("Status", db.NVARCHAR(50), nullable=False, default="In Progress")
    budget = db.Column("Budget", db.Numeric(18, 2), nullable=True)

    assignments = db.relationship(
        "ProjectAssignment",
        back_populates="project",
        cascade="all, delete-orphan",
    )


class Employee(UserMixin, db.Model):
    __tablename__ = "Employees"

    id = db.Column("ID", db.Integer, primary_key=True)
    full_name = db.Column("FullName", db.NVARCHAR(255), nullable=False)
    position = db.Column("Position", db.NVARCHAR(255), nullable=True)
    email = db.Column("Email", db.NVARCHAR(255), nullable=True, unique=True)
    password_hash = db.Column("PasswordHash", db.NVARCHAR(255), nullable=True)
    role = db.Column("Role", db.NVARCHAR(20), nullable=False, default="Worker")  # Admin / Worker
    avatar_filename = db.Column("AvatarFilename", db.NVARCHAR(255), nullable=True)

    assignments = db.relationship(
        "ProjectAssignment",
        backref="employee",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)


class ProjectAssignment(db.Model):
    __tablename__ = "ProjectAssignments"

    project_id = db.Column("ProjectID", db.Integer, db.ForeignKey("Projects.ID"), primary_key=True)
    employee_id = db.Column("EmployeeID", db.Integer, db.ForeignKey("Employees.ID"), primary_key=True)

    project = db.relationship("Project", back_populates="assignments")


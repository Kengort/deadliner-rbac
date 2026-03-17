import os
from urllib.parse import quote_plus


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")

    _raw_odbc_str = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=localhost;"
        "DATABASE=ProjectDB;"
        "Trusted_Connection=yes;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        f"mssql+pyodbc:///?odbc_connect={quote_plus(_raw_odbc_str)}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False



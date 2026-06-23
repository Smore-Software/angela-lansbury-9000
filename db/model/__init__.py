import os

from sqla_wrapper import SQLAlchemy

DB = SQLAlchemy(os.environ.get('DATABASE_URL', 'sqlite:///bumper-db.sqlite'))

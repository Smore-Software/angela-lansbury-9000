import os

from sqla_wrapper import SQLAlchemy

# pool_pre_ping: prod talks to the Supabase session pooler, which drops idle
# connections; without a checkout-time ping a dead connection surfaces as an
# error mid-operation. pool_recycle retires connections before that happens.
DB = SQLAlchemy(
    os.environ.get('DATABASE_URL', 'sqlite:///bumper-db.sqlite'),
    engine_options={'pool_pre_ping': True, 'pool_recycle': 1800},
)

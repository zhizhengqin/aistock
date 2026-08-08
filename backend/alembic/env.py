from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from app.core.config import settings
from app.models.base import Base
from app.models import user, llm_usage, task_record, analysis_report  # noqa: F401

config = context.config
from app.models import main_force_run, sector_report, dragon_tiger_report  # noqa: F401
from app.models import portfolio_stock, portfolio_report, monitor_config, monitor_notification, risk_warning  # noqa: F401
from app.models import news_item, us_research_report  # noqa: F401
from app.models import membership_plan, usage_log  # noqa: F401
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

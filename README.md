# Angela Lansbury 9000

For the bot's command reference, go to the [Wiki](https://github.com/calebdinsmore/angela-lansbury-9000/wiki).

## Setup

### Install necessary Python tools

1. Install **Python 3.10**
2. **Recommended:** Get [pipx](https://pypa.github.io/pipx/) to manage global Python packages.
3. Install **pipenv**
   1. `pipx install pipenv`

### Environment setup

Once you've cloned the repo, run
```shell
pipenv install
```

Next, create a `.env` file at the root of the repo. The easiest way is to copy
the provided template:
```shell
cp .env.example .env
```
At minimum, set `BOT_TOKEN` to your own test bot token. If you don't know how
to make one, follow Step 1 on [this page](https://discord.com/developers/docs/getting-started#step-1-creating-an-app).
`DATABASE_URL` controls which database the bot uses — see the
[Database](#database) section below.

Finally, initialize the DB by running this at the root of the repo:
```shell
pipenv run python cli.py db create_all
```

### Database

The bot reads its connection string from the `DATABASE_URL` environment variable
(a SQLAlchemy URL). If it's unset, the bot falls back to a local SQLite file
(`sqlite:///bumper-db.sqlite`), which is fine for quick local hacking.

For prod parity, develop against a real Postgres. A `docker-compose.yml` is
provided that runs **Postgres 15** (the same major version Supabase runs) with a
named volume for persistence:

```shell
docker compose up -d           # start Postgres in the background
```

Then point the bot at it (this is the dev default in `.env.example`):
```
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/bumper"
```

Run `pipenv run python cli.py db create_all` once to build the schema, then start
the bot as usual. Stop the database with `docker compose down` (data persists) or
`docker compose down -v` (wipe the volume).

**Production (Supabase):** set `DATABASE_URL` to your project's **pooled**
(PgBouncer / transaction-mode) connection string:
```
DATABASE_URL="postgresql+psycopg://USER:PASS@HOST:PORT/postgres"
```
No application code changes are needed to switch databases — only this env var.
The driver is [psycopg 3](https://www.psycopg.org/) (`postgresql+psycopg://`),
which is synchronous.

#### Production cutover to Supabase (one-time)

Moving live data off the old SQLite file onto Supabase. The migration mechanics
and validation were rehearsed end-to-end against a local Postgres 15 using the
real prod data (all checks passed); this is the owner-run production cutover.

1. **Stop the bot** so no writes land mid-migration. The live
   `bumper-db.sqlite` is now the frozen **rollback artifact** — don't modify or
   delete it (keep it until the bot has run cleanly on Supabase for a few days).
2. **Build the schema on the empty Supabase DB:**
   ```shell
   export DATABASE_URL='postgresql+psycopg://USER:PASS@HOST:PORT/postgres'
   pipenv run python cli.py db upgrade      # alembic upgrade head
   pipenv run python cli.py db current      # prints the baseline revision (head)
   ```
   If Supabase isn't empty from a prior attempt, reset first in the Studio SQL
   editor: `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` then re-run `db upgrade`.
3. **Copy the data** (real run, not `--dry-run`):
   ```shell
   pipenv run python scripts/migrate_sqlite_to_postgres.py --sqlite ./bumper-db.sqlite
   ```
   It copies tables in FK-safe order, expands the old `excluded_channels` CSV
   into `activity_excluded_channel` rows, lets Postgres assign the `birthdays`
   surrogate `id`, advances identity sequences past the copied ids, and prints a
   per-table row-count table. **Save that output.**
4. **Verify row counts** against the SQLite source — every table must match
   except the two intentional transforms: `activity_excluded_channel` equals the
   **sum of CSV ids** across all `activity_module_settings` rows, and `birthdays`
   keeps its count but gets fresh `id`s.
5. **Cut over:** set the production `DATABASE_URL` to the Supabase pooled string
   and restart the bot. This is the only change — no code edits.
6. **Smoke-test live:**
   - *Starboard race path:* react a message over the threshold; duplicate /
     concurrent reactions must **not** error. Postgres raises a duplicate
     `(starboard_config_id, original_message_id)` as the same
     `sa.exc.IntegrityError` caught in `db/helpers/starboard_helper.py:160`
     (rollback → update the existing row).
   - *Birthdays:* add one; the same `(user, name)` is rejected, a different name
     for the same user coexists.
   - *Activity exclusions:* exclude a channel and confirm it sticks.
7. **Supabase Studio (the user-facing goal):** open **Table Editor**, confirm
   all 17 data tables are visible, and edit a value (e.g. a `guild_config`
   field) to confirm rows are editable.
8. **Rollback** (if needed): stop the bot, point `DATABASE_URL` back at
   `sqlite:///bumper-db.sqlite` (or unset it — that's the fallback default), and
   restart. No data is lost; the SQLite file was never modified.

> Supabase free tier pauses a DB after ~7 days of inactivity. The always-on bot
> keeps it warm; if a long quiet period ever pauses it, a periodic keepalive
> `SELECT 1` is the fallback.

### Running the bot

You can run the bot from your terminal using
```shell
pipenv run python main.py
```

If everything's working, you should see this appear after a few seconds:
```
Logged in as
Your Bot Name
12345678900982345
------
```

Except it should show the name of your bot and its ID.

### Running tests

The test suite uses [pytest](https://docs.pytest.org/) and
[pytest-asyncio](https://pytest-asyncio.readthedocs.io/). Install the dev
dependencies (once) and run the suite:

```shell
pipenv install --dev
pipenv run pytest
```

Tests never touch the real `bumper-db.sqlite`. The DB URL is read from the
`DATABASE_URL` environment variable (defaulting to the production sqlite file),
and `tests/conftest.py` points it at a throwaway temp database before any model
is imported. To run against a specific database yourself, override it:

```shell
DATABASE_URL="sqlite:///some-other.sqlite" pipenv run pytest
```

The suite always runs on SQLite. There's also an optional Postgres connectivity
smoke check that's skipped unless you point it at a live Postgres via
`TEST_PG_URL` (e.g. the local Docker instance above):

```shell
TEST_PG_URL="postgresql+psycopg://postgres:postgres@localhost:5432/bumper" pipenv run pytest
```

---

## Important packages to know about

### nextcord ([Docs](https://docs.nextcord.dev/en/stable/index.html))

Nextcord is the Python SDK for Discord that the bot uses. It's big and supports
all (as far as I know anyway) of Discord's features.

Its [commands framework](https://docs.nextcord.dev/en/stable/ext/commands/index.html) is
important to be familiar with, as well as this primer on [Slash Commands](https://docs.nextcord.dev/en/stable/interactions.html).

### sqla-wrapper ([Docs](https://sqla-wrapper.scaletti.dev/))

I use sqla-wrapper for most of my personal Python projects that need data persistence,
since working with SQLAlchemy and Alembic directly is kind of a pain, lol.

---

## Quick primer on how I've organized the code

### Entry point

The entry point for the bot is `main.py`. All this does right now is call the `run`
function in `bot/app.py`, which is where the bot configuration/instantiation
lives.

### Cogs

The main way nextcord lets you organize groups of commands is via [Cogs](https://docs.nextcord.dev/en/stable/ext/commands/cogs.html).

`bot/cogs` is where I put them, and an example cog is `bot/cogs/auto_delete/auto_delete_commands.py`.
This is where the `/auto-delete` slash commands are registered, as well as the
looping task that looks for messages to delete.

### The DB

Models are configured in `db/model`. Whenever you make a new model, if you want
that to be picked up by the `create_all` CLI function, you need to import it in
`db/__init__.py`.

As I mentioned earlier, I use SQLAlchemy as the ORM to streamline data persistence,
and I use Alembic to handle DB migrations. Refer to the linked `sqla-wrapper` docs above
for information about how to add new models. 

`sqla-wrapper` also wraps Alembic's CLI tools, which I've set up in `cli.py`.
If you run `pipenv run python cli.py` you should see an output with all the 
functions you can run.

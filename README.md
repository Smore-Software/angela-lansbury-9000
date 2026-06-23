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

Once that completes, initialize the DB by running this at the root of the repo:
```shell
pipenv run python cli.py db create_all
```

Finally, create a `.env` file at the root of the repo with the following contents:
```
BOT_TOKEN="<YOUR_BOT_TOKEN>"
```
Replace `<YOUR_BOT_TOKEN>` with your own test bot token. If you don't know how
to make one, follow Step 1 on [this page](https://discord.com/developers/docs/getting-started#step-1-creating-an-app).

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

from dynaconf import Dynaconf, Validator


def load_settings():
    settings = Dynaconf(
        settings_files=["config/config.json", "config/.secrets.json"],
        environments=True,
        load_dotenv=True,
        env_switcher="ENV_FOR_DYNACONF",
        dotenv_path="config/.dynaenv",
    )

    settings.validators.register(Validator("DISCORD_TOKEN", "DATABASE_ADDRESS", "OWNER_IDS", must_exist=True))

    settings.validators.validate()

    return settings

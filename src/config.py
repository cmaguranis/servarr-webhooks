import os
import configparser

CONFIG_PATH = os.getenv("CONFIG_PATH", "/config/config.ini")


def get(section, key, fallback=None):
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH)
    return cfg.get(section, key, fallback=fallback)


def get_list(section, key, fallback=""):
    raw = get(section, key, fallback=fallback)
    return [v.strip().lower() for v in raw.split(",") if v.strip()]

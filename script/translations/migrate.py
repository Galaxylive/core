"""Migrate things."""
import json
import pathlib
from pprint import pprint
import re

from .const import CORE_PROJECT_ID, FRONTEND_PROJECT_ID, INTEGRATIONS_DIR
from .lokalise import get_api

FRONTEND_REPO = pathlib.Path("../frontend/")


def create_lookup(results):
    """Create a lookup table by key name."""
    return {key["key_name"]["web"]: key for key in results}


def rename_keys(project_id, to_migrate):
    """Rename keys.

    to_migrate is Dict[from_key] = to_key.
    """
    updates = []

    lokalise = get_api(project_id)

    from_key_data = lokalise.keys_list({"filter_keys": ",".join(to_migrate)})
    if len(from_key_data) != len(to_migrate):
        print(
            f"Lookin up keys in Lokalise returns {len(from_key_data)} results, expected {len(to_migrate)}"
        )
        return

    from_key_lookup = create_lookup(from_key_data)

    print("Gathering IDs")

    for from_key, to_key in to_migrate.items():
        updates.append(
            {"key_id": from_key_lookup[from_key]["key_id"], "key_name": to_key}
        )

    pprint(updates)

    print()
    while input("Type YES to confirm: ") != "YES":
        pass

    print()
    print("Updating keys")
    pprint(lokalise.keys_bulk_update(updates))


def migrate_project_keys_translations(from_project_id, to_project_id, to_migrate):
    """Migrate keys and translations from one project to another.

    to_migrate is Dict[from_key] = to_key.
    """
    from_lokalise = get_api(from_project_id)
    to_lokalise = get_api(to_project_id, True)

    from_key_data = from_lokalise.keys_list(
        {"filter_keys": ",".join(to_migrate), "include_translations": 1}
    )
    if len(from_key_data) != len(to_migrate):
        print(
            f"Lookin up keys in Lokalise returns {len(from_key_data)} results, expected {len(to_migrate)}"
        )
        return

    from_key_lookup = create_lookup(from_key_data)

    # Fetch keys in target
    # We are going to skip migrating existing keys
    to_key_data = to_lokalise.keys_list(
        {"filter_keys": ",".join(to_migrate.values()), "include_translations": 1}
    )
    existing = set(create_lookup(to_key_data))

    missing = [key for key in to_migrate.values() if key not in existing]

    if not missing:
        print("All keys to migrate exist already, nothing to do")
        return

    print("Creating", ", ".join(missing))
    to_key_lookup = create_lookup(
        to_lokalise.keys_create(
            [{"key_name": key, "platforms": ["web"]} for key in missing]
        )
    )

    updates = []

    for from_key, to_key in to_migrate.items():
        # If it is not in lookup, it already existed, skipping it.
        if to_key not in to_key_lookup:
            continue

        updates.append(
            {
                "key_id": to_key_lookup[to_key]["key_id"],
                "translations": [
                    {
                        "language_iso": from_translation["language_iso"],
                        "translation": from_translation["translation"],
                        "is_reviewed": from_translation["is_reviewed"],
                        "is_fuzzy": from_translation["is_fuzzy"],
                    }
                    for from_translation in from_key_lookup[from_key]["translations"]
                ],
            }
        )

    print("Updating")
    pprint(updates)
    print()
    print()
    pprint(to_lokalise.keys_bulk_update(updates))


def find_and_rename_keys():
    """Find and rename keys in core."""
    to_migrate = {}

    for integration in INTEGRATIONS_DIR.iterdir():
        strings_file = integration / "strings.json"
        if not strings_file.is_file():
            continue

        strings = json.loads(strings_file.read_text())

        if "title" in strings.get("config", {}):
            from_key = f"component::{integration.name}::config::title"
            to_key = f"component::{integration.name}::title"
            to_migrate[from_key] = to_key

    rename_keys(CORE_PROJECT_ID, to_migrate)


def find_different_languages():
    """Find different supported languages."""
    core_api = get_api(CORE_PROJECT_ID)
    frontend_api = get_api(FRONTEND_PROJECT_ID)

    core_languages = {lang["lang_iso"] for lang in core_api.languages_list()}
    frontend_languages = {lang["lang_iso"] for lang in frontend_api.languages_list()}

    print("Core minus frontend", core_languages - frontend_languages)
    print("Frontend minus core", frontend_languages - core_languages)


def interactive_update():
    """Interactive update integration strings."""
    for integration in INTEGRATIONS_DIR.iterdir():
        strings_file = integration / "strings.json"

        if not strings_file.is_file():
            continue

        strings = json.loads(strings_file.read_text())

        if "title" not in strings:
            continue

        manifest = json.loads((integration / "manifest.json").read_text())

        print("Processing", manifest["name"])
        print("Translation title", strings["title"])
        if input("Drop title? (1=yes, 2=no) ") == "1":
            strings.pop("title")
            strings_file.write_text(json.dumps(strings))
        print()


STATE_REWRITE = {
    "Off": "[%key:common::state::off%]",
    "On": "[%key:common::state::on%]",
    "Unknown": "[%key:common::state::unknown%]",
    "Unavailable": "[%key:common::state::unavailable%]",
    "Open": "[%key:common::state::open%]",
    "Closed": "[%key:common::state::closed%]",
    "Connected": "[%key:common::state::connected%]",
    "Disconnected": "[%key:common::state::disconnected%]",
    "Locked": "[%key:common::state::locked%]",
    "Unlocked": "[%key:common::state::unlocked%]",
    "Active": "[%key:common::state::idle%]",
    "Idle": "[%key:common::state::auto%]",
    "Standby": "[%key:common::state::standby%]",
    "Home": "[%key:common::state::home%]",
    "Away": "[%key:common::state::not_home%]",
    "[%key:state::default::off%]": "[%key:common::state::off%]",
    "[%key:state::default::on%]": "[%key:common::state::on%]",
    "[%key:state::cover::open%]": "[%key:common::state::open%]",
    "[%key:state::cover::closed%]": "[%key:common::state::closed%]",
    "[%key:state::lock::locked%]": "[%key:common::state::locked%]",
    "[%key:state::lock::unlocked%]": "[%key:common::state::unlocked%]",
}
SKIP_DOMAIN = {"default", "scene"}
STATES_WITH_DEV_CLASS = {"binary_sensor", "zwave"}


def find_frontend_states():
    """Find frontend states.

    Source key -> target key
    Add key to integrations strings.json
    """
    frontend_states = json.loads(
        (FRONTEND_REPO / "src/translations/en.json").read_text()
    )["state"]

    # domain => state object
    to_write = {}

    for domain, states in frontend_states.items():
        if domain in SKIP_DOMAIN:
            continue

        if domain in STATES_WITH_DEV_CLASS:
            domain_to_write = dict(states)

            # Rewrite "default" device class to _
            if "default" in domain_to_write:
                domain_to_write["_"] = domain_to_write.pop("default")
        else:
            domain_to_write = {"_": states}

        # Map out common values with
        for dev_class_states in domain_to_write.values():
            for key, value in dev_class_states.copy().items():
                if value in STATE_REWRITE:
                    dev_class_states[key] = STATE_REWRITE[value]
                    continue

                match = re.match(r"\[\%key:state::(\w+)::(.+)\%\]", value)

                if not match:
                    continue

                dev_class_states[key] = "[%key:component::{}::state::{}%]".format(
                    *match.groups()
                )

        to_write[domain] = domain_to_write

        # TODO create to_migrate dict for migrate_project_keys_translations

    for domain, state in to_write.items():
        strings = INTEGRATIONS_DIR / domain / "strings.json"
        if strings.is_file():
            content = json.loads(strings.read_text())
        else:
            content = {}

        content["state"] = state
        strings.write_text(json.dumps(content, indent=2) + "\n")


def run():
    """Migrate translations."""
    # Import new common keys
    # migrate_project_keys_translations(
    #     FRONTEND_PROJECT_ID,
    #     CORE_PROJECT_ID,
    #     {
    #         "state::default::off": "common::state::off",
    #         "state::default::on": "common::state::on",
    #         "state::default::unknown": "common::state::unknown",
    #         "state::default::unavailable": "common::state::unavailable",
    #         "state::cover::open": "common::state::open",
    #         "state::cover::closed": "common::state::closed",
    #         "state::binary_sensor::connectivity::on": "common::state::connected",
    #         "state::binary_sensor::connectivity::off": "common::state::disconnected",
    #         "state::lock::locked": "common::state::locked",
    #         "state::lock::unlocked": "common::state::unlocked",
    #         "state::camera::idle": "common::state::idle",
    #         "state::climate::auto": "common::state::auto",
    #         "state::media_player::standby": "common::state::standby",
    #     },
    # )

    find_frontend_states()

    return 0

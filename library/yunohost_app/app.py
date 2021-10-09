# -*- coding: utf-8 -*-

""" License

    Copyright (C) 2013 YunoHost

    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU Affero General Public License as published
    by the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Affero General Public License for more details.

    You should have received a copy of the GNU Affero General Public License
    along with this program; if not, see http://www.gnu.org/licenses

"""

""" yunohost_app.py

    Manage apps
"""
import os
import toml
import json
import shutil
import yaml
import time
import re
import subprocess
import tempfile
from collections import OrderedDict
from typing import List, Tuple, Dict, Any

from moulinette import Moulinette, m18n
from moulinette.utils.log import getActionLogger
from moulinette.utils.process import run_commands, check_output
from moulinette.utils.filesystem import (
    read_file,
    read_json,
    read_toml,
    read_yaml,
    write_to_file,
    write_to_json,
    cp,
    rm,
    chown,
    chmod,
)

from yunohost.utils import packages
from yunohost.utils.config import (
    ConfigPanel,
    ask_questions_and_parse_answers,
    DomainQuestion,
    PathQuestion,
)
from yunohost.utils.i18n import _value_for_locale
from yunohost.utils.error import YunohostError, YunohostValidationError
from yunohost.utils.filesystem import free_space_in_directory
from yunohost.log import is_unit_operation, OperationLogger
from yunohost.app_catalog import (  # noqa
    app_catalog,
    app_search,
    _load_apps_catalog,
    app_fetchlist,
)

logger = getActionLogger("yunohost.app")

APPS_SETTING_PATH = "/etc/yunohost/apps/"
APP_TMP_WORKDIRS = "/var/cache/yunohost/app_tmp_work_dirs"

re_app_instance_name = re.compile(
    r"^(?P<appid>[\w-]+?)(__(?P<appinstancenb>[1-9][0-9]*))?$"
)

APP_REPO_URL = re.compile(
    r"^https://[a-zA-Z0-9-_.]+/[a-zA-Z0-9-_./]+/[a-zA-Z0-9-_.]+_ynh(/?(-/)?tree/[a-zA-Z0-9-_.]+)?(\.git)?/?$"
)

APP_FILES_TO_COPY = [
    "manifest.json",
    "manifest.toml",
    "actions.json",
    "actions.toml",
    "config_panel.toml",
    "scripts",
    "conf",
    "hooks",
    "doc",
]


def app_list(full=False, installed=False, filter=None):
    """
    List installed apps
    """

    # Old legacy argument ... app_list was a combination of app_list and
    # app_catalog before 3.8 ...
    if installed:
        logger.warning(
            "Argument --installed ain't needed anymore when using 'yunohost app list'. It directly returns the list of installed apps.."
        )

    # Filter is a deprecated option...
    if filter:
        logger.warning(
            "Using -f $appname in 'yunohost app list' is deprecated. Just use 'yunohost app list | grep -q 'id: $appname' to check a specific app is installed"
        )

    out = []
    for app_id in sorted(_installed_apps()):

        if filter and not app_id.startswith(filter):
            continue

        try:
            app_info_dict = app_info(app_id, full=full)
        except Exception as e:
            logger.error("Failed to read info for %s : %s" % (app_id, e))
            continue
        app_info_dict["id"] = app_id
        out.append(app_info_dict)

    return {"apps": out}


def app_info(app, full=False):
    """
    Get info for a specific app
    """
    from yunohost.permission import user_permission_list

    _assert_is_installed(app)

    setting_path = os.path.join(APPS_SETTING_PATH, app)
    local_manifest = _get_manifest_of_app(setting_path)
    permissions = user_permission_list(full=True, absolute_urls=True, apps=[app])[
        "permissions"
    ]

    settings = _get_app_settings(app)

    ret = {
        "description": _value_for_locale(local_manifest["description"]),
        "name": permissions.get(app + ".main", {}).get("label", local_manifest["name"]),
        "version": local_manifest.get("version", "-"),
    }

    if "domain" in settings and "path" in settings:
        ret["domain_path"] = settings["domain"] + settings["path"]

    if not full:
        return ret

    ret["setting_path"] = setting_path
    ret["manifest"] = local_manifest
    ret["manifest"]["arguments"] = _set_default_ask_questions(
        ret["manifest"].get("arguments", {})
    )
    ret["settings"] = settings

    absolute_app_name, _ = _parse_app_instance_name(app)
    ret["from_catalog"] = _load_apps_catalog()["apps"].get(absolute_app_name, {})
    ret["upgradable"] = _app_upgradable(ret)

    ret["is_webapp"] = "domain" in settings and "path" in settings

    ret["supports_change_url"] = os.path.exists(
        os.path.join(setting_path, "scripts", "change_url")
    )
    ret["supports_backup_restore"] = os.path.exists(
        os.path.join(setting_path, "scripts", "backup")
    ) and os.path.exists(os.path.join(setting_path, "scripts", "restore"))
    ret["supports_multi_instance"] = is_true(
        local_manifest.get("multi_instance", False)
    )
    ret["supports_config_panel"] = os.path.exists(
        os.path.join(setting_path, "config_panel.toml")
    )

    ret["permissions"] = permissions
    ret["label"] = permissions.get(app + ".main", {}).get("label")

    if not ret["label"]:
        logger.warning("Failed to get label for app %s ?" % app)
    return ret


def _app_upgradable(app_infos):
    from packaging import version

    # Determine upgradability

    app_in_catalog = app_infos.get("from_catalog")
    installed_version = version.parse(app_infos.get("version", "0~ynh0"))
    version_in_catalog = version.parse(
        app_infos.get("from_catalog", {}).get("manifest", {}).get("version", "0~ynh0")
    )

    if not app_in_catalog:
        return "url_required"

    # Do not advertise upgrades for bad-quality apps
    level = app_in_catalog.get("level", -1)
    if (
        not (isinstance(level, int) and level >= 5)
        or app_in_catalog.get("state") != "working"
    ):
        return "bad_quality"

    # If the app uses the standard version scheme, use it to determine
    # upgradability
    if "~ynh" in str(installed_version) and "~ynh" in str(version_in_catalog):
        if installed_version < version_in_catalog:
            return "yes"
        else:
            return "no"

    # Legacy stuff for app with old / non-standard version numbers...

    # In case there is neither update_time nor install_time, we assume the app can/has to be upgraded
    if not app_infos["from_catalog"].get("lastUpdate") or not app_infos[
        "from_catalog"
    ].get("git"):
        return "url_required"

    settings = app_infos["settings"]
    local_update_time = settings.get("update_time", settings.get("install_time", 0))
    if app_infos["from_catalog"]["lastUpdate"] > local_update_time:
        return "yes"
    else:
        return "no"


def app_map(app=None, raw=False, user=None):
    """
    Returns a map of url <-> app id such as :

    {
       "domain.tld/foo": "foo__2",
       "domain.tld/mail: "rainloop",
       "other.tld/": "bar",
       "sub.other.tld/pwet": "pwet",
    }

    When using "raw", the structure changes to :

    {
        "domain.tld": {
            "/foo": {"label": "App foo", "id": "foo__2"},
            "/mail": {"label": "Rainloop", "id: "rainloop"},
        },
        "other.tld": {
            "/": {"label": "Bar", "id": "bar"},
        },
        "sub.other.tld": {
            "/pwet": {"label": "Pwet", "id": "pwet"}
        }
    }
    """

    from yunohost.permission import user_permission_list

    apps = []
    result = {}

    if app is not None:
        if not _is_installed(app):
            raise YunohostValidationError(
                "app_not_installed", app=app, all_apps=_get_all_installed_apps_id()
            )
        apps = [
            app,
        ]
    else:
        apps = _installed_apps()

    permissions = user_permission_list(full=True, absolute_urls=True, apps=apps)[
        "permissions"
    ]
    for app_id in apps:
        app_settings = _get_app_settings(app_id)
        if not app_settings:
            continue
        if "domain" not in app_settings:
            continue
        if "path" not in app_settings:
            # we assume that an app that doesn't have a path doesn't have an HTTP api
            continue
        # This 'no_sso' settings sound redundant to not having $path defined ....
        # At least from what I can see, all apps using it don't have a path defined ...
        if (
            "no_sso" in app_settings
        ):  # I don't think we need to check for the value here
            continue
        # Users must at least have access to the main permission to have access to extra permissions
        if user:
            if not app_id + ".main" in permissions:
                logger.warning(
                    "Uhoh, no main permission was found for app %s ... sounds like an app was only partially removed due to another bug :/"
                    % app_id
                )
                continue
            main_perm = permissions[app_id + ".main"]
            if user not in main_perm["corresponding_users"]:
                continue

        this_app_perms = {
            p: i
            for p, i in permissions.items()
            if p.startswith(app_id + ".") and (i["url"] or i["additional_urls"])
        }

        for perm_name, perm_info in this_app_perms.items():
            # If we're building the map for a specific user, check the user
            # actually is allowed for this specific perm
            if user and user not in perm_info["corresponding_users"]:
                continue

            perm_label = perm_info["label"]
            perm_all_urls = (
                []
                + ([perm_info["url"]] if perm_info["url"] else [])
                + perm_info["additional_urls"]
            )

            for url in perm_all_urls:

                # Here, we decide to completely ignore regex-type urls ...
                # Because :
                # - displaying them in regular "yunohost app map" output creates
                # a pretty big mess when there are multiple regexes for the same
                # app ? (c.f. for example lufi)
                # - it doesn't really make sense when checking app conflicts to
                # compare regexes ? (Or it could in some cases but ugh ?)
                #
                if url.startswith("re:"):
                    continue

                if not raw:
                    result[url] = perm_label
                else:
                    if "/" in url:
                        perm_domain, perm_path = url.split("/", 1)
                        perm_path = "/" + perm_path
                    else:
                        perm_domain = url
                        perm_path = "/"
                    if perm_domain not in result:
                        result[perm_domain] = {}
                    result[perm_domain][perm_path] = {"label": perm_label, "id": app_id}

    return result


@is_unit_operation()
def app_change_url(operation_logger, app, domain, path):
    """
    Modify the URL at which an application is installed.

    Keyword argument:
        app -- Taget app instance name
        domain -- New app domain on which the application will be moved
        path -- New path at which the application will be move

    """
    from yunohost.hook import hook_exec, hook_callback
    from yunohost.service import service_reload_or_restart

    installed = _is_installed(app)
    if not installed:
        raise YunohostValidationError(
            "app_not_installed", app=app, all_apps=_get_all_installed_apps_id()
        )

    if not os.path.exists(
        os.path.join(APPS_SETTING_PATH, app, "scripts", "change_url")
    ):
        raise YunohostValidationError("app_change_url_no_script", app_name=app)

    old_domain = app_setting(app, "domain")
    old_path = app_setting(app, "path")

    # Normalize path and domain format

    domain = DomainQuestion.normalize(domain)
    old_domain = DomainQuestion.normalize(old_domain)
    path = PathQuestion.normalize(path)
    old_path = PathQuestion.normalize(old_path)

    if (domain, path) == (old_domain, old_path):
        raise YunohostValidationError(
            "app_change_url_identical_domains", domain=domain, path=path
        )

    app_setting_path = os.path.join(APPS_SETTING_PATH, app)
    path_requirement = _guess_webapp_path_requirement(app_setting_path)
    _validate_webpath_requirement(
        {"domain": domain, "path": path}, path_requirement, ignore_app=app
    )

    tmp_workdir_for_app = _make_tmp_workdir_for_app(app=app)

    # Prepare env. var. to pass to script
    env_dict = _make_environment_for_app_script(app, workdir=tmp_workdir_for_app)
    env_dict["YNH_APP_OLD_DOMAIN"] = old_domain
    env_dict["YNH_APP_OLD_PATH"] = old_path
    env_dict["YNH_APP_NEW_DOMAIN"] = domain
    env_dict["YNH_APP_NEW_PATH"] = path

    if domain != old_domain:
        operation_logger.related_to.append(("domain", old_domain))
    operation_logger.extra.update({"env": env_dict})
    operation_logger.start()

    change_url_script = os.path.join(tmp_workdir_for_app, "scripts/change_url")

    # Execute App change_url script
    ret = hook_exec(change_url_script, env=env_dict)[0]
    if ret != 0:
        msg = "Failed to change '%s' url." % app
        logger.error(msg)
        operation_logger.error(msg)

        # restore values modified by app_checkurl
        # see begining of the function
        app_setting(app, "domain", value=old_domain)
        app_setting(app, "path", value=old_path)
        return
    shutil.rmtree(tmp_workdir_for_app)

    # this should idealy be done in the change_url script but let's avoid common mistakes
    app_setting(app, "domain", value=domain)
    app_setting(app, "path", value=path)

    app_ssowatconf()

    service_reload_or_restart("nginx")

    logger.success(m18n.n("app_change_url_success", app=app, domain=domain, path=path))

    hook_callback("post_app_change_url", env=env_dict)


def app_upgrade(app=[], url=None, file=None, force=False, no_safety_backup=False):
    """
    Upgrade app

    Keyword argument:
        file -- Folder or tarball for upgrade
        app -- App(s) to upgrade (default all)
        url -- Git url to fetch for upgrade
        no_safety_backup -- Disable the safety backup during upgrade

    """
    from packaging import version
    from yunohost.hook import (
        hook_add,
        hook_remove,
        hook_callback,
        hook_exec_with_script_debug_if_failure,
    )
    from yunohost.permission import permission_sync_to_user
    from yunohost.regenconf import manually_modified_files
    from yunohost.utils.legacy import _patch_legacy_php_versions, _patch_legacy_helpers

    apps = app
    # Check if disk space available
    if free_space_in_directory("/") <= 512 * 1000 * 1000:
        raise YunohostValidationError("disk_space_not_sufficient_update")
    # If no app is specified, upgrade all apps
    if not apps:
        # FIXME : not sure what's supposed to happen if there is a url and a file but no apps...
        if not url and not file:
            apps = _installed_apps()
    elif not isinstance(app, list):
        apps = [app]

    # Remove possible duplicates
    apps = [app_ for i, app_ in enumerate(apps) if app_ not in apps[:i]]

    # Abort if any of those app is in fact not installed..
    for app_ in apps:
        _assert_is_installed(app_)

    if len(apps) == 0:
        raise YunohostValidationError("apps_already_up_to_date")
    if len(apps) > 1:
        logger.info(m18n.n("app_upgrade_several_apps", apps=", ".join(apps)))

    for number, app_instance_name in enumerate(apps):
        logger.info(m18n.n("app_upgrade_app_name", app=app_instance_name))

        app_dict = app_info(app_instance_name, full=True)

        if file and isinstance(file, dict):
            # We use this dirty hack to test chained upgrades in unit/functional tests
            new_app_src = file[app_instance_name]
        elif file:
            new_app_src = file
        elif url:
            new_app_src = url
        elif app_dict["upgradable"] == "url_required":
            logger.warning(m18n.n("custom_app_url_required", app=app_instance_name))
            continue
        elif app_dict["upgradable"] == "yes" or force:
            new_app_src = app_dict["manifest"]["id"]
        else:
            logger.success(m18n.n("app_already_up_to_date", app=app_instance_name))
            continue

        manifest, extracted_app_folder = _extract_app(new_app_src)

        # Manage upgrade type and avoid any upgrade if there is nothing to do
        upgrade_type = "UNKNOWN"
        # Get current_version and new version
        app_new_version = version.parse(manifest.get("version", "?"))
        app_current_version = version.parse(app_dict.get("version", "?"))
        if "~ynh" in str(app_current_version) and "~ynh" in str(app_new_version):
            if app_current_version >= app_new_version and not force:
                # In case of upgrade from file or custom repository
                # No new version available
                logger.success(m18n.n("app_already_up_to_date", app=app_instance_name))
                # Save update time
                now = int(time.time())
                app_setting(app_instance_name, "update_time", now)
                app_setting(
                    app_instance_name,
                    "current_revision",
                    manifest.get("remote", {}).get("revision", "?"),
                )
                continue
            elif app_current_version > app_new_version:
                upgrade_type = "DOWNGRADE_FORCED"
            elif app_current_version == app_new_version:
                upgrade_type = "UPGRADE_FORCED"
            else:
                app_current_version_upstream, app_current_version_pkg = str(
                    app_current_version
                ).split("~ynh")
                app_new_version_upstream, app_new_version_pkg = str(
                    app_new_version
                ).split("~ynh")
                if app_current_version_upstream == app_new_version_upstream:
                    upgrade_type = "UPGRADE_PACKAGE"
                elif app_current_version_pkg == app_new_version_pkg:
                    upgrade_type = "UPGRADE_APP"
                else:
                    upgrade_type = "UPGRADE_FULL"

        # Check requirements
        _check_manifest_requirements(manifest)
        _assert_system_is_sane_for_app(manifest, "pre")

        app_setting_path = os.path.join(APPS_SETTING_PATH, app_instance_name)

        # Prepare env. var. to pass to script
        env_dict = _make_environment_for_app_script(
            app_instance_name, workdir=extracted_app_folder
        )
        env_dict["YNH_APP_UPGRADE_TYPE"] = upgrade_type
        env_dict["YNH_APP_MANIFEST_VERSION"] = str(app_new_version)
        env_dict["YNH_APP_CURRENT_VERSION"] = str(app_current_version)
        env_dict["NO_BACKUP_UPGRADE"] = "1" if no_safety_backup else "0"

        # We'll check that the app didn't brutally edit some system configuration
        manually_modified_files_before_install = manually_modified_files()

        # Attempt to patch legacy helpers ...
        _patch_legacy_helpers(extracted_app_folder)

        # Apply dirty patch to make php5 apps compatible with php7
        _patch_legacy_php_versions(extracted_app_folder)

        # Start register change on system
        related_to = [("app", app_instance_name)]
        operation_logger = OperationLogger("app_upgrade", related_to, env=env_dict)
        operation_logger.start()

        # Execute the app upgrade script
        upgrade_failed = True
        try:
            (
                upgrade_failed,
                failure_message_with_debug_instructions,
            ) = hook_exec_with_script_debug_if_failure(
                extracted_app_folder + "/scripts/upgrade",
                env=env_dict,
                operation_logger=operation_logger,
                error_message_if_script_failed=m18n.n("app_upgrade_script_failed"),
                error_message_if_failed=lambda e: m18n.n(
                    "app_upgrade_failed", app=app_instance_name, error=e
                ),
            )
        finally:
            # Whatever happened (install success or failure) we check if it broke the system
            # and warn the user about it
            try:
                broke_the_system = False
                _assert_system_is_sane_for_app(manifest, "post")
            except Exception as e:
                broke_the_system = True
                logger.error(
                    m18n.n("app_upgrade_failed", app=app_instance_name, error=str(e))
                )
                failure_message_with_debug_instructions = operation_logger.error(str(e))

            # We'll check that the app didn't brutally edit some system configuration
            manually_modified_files_after_install = manually_modified_files()
            manually_modified_files_by_app = set(
                manually_modified_files_after_install
            ) - set(manually_modified_files_before_install)
            if manually_modified_files_by_app:
                logger.error(
                    "Packagers /!\\ This app manually modified some system configuration files! This should not happen! If you need to do so, you should implement a proper conf_regen hook. Those configuration were affected:\n    - "
                    + "\n     -".join(manually_modified_files_by_app)
                )

            # If upgrade failed or broke the system,
            # raise an error and interrupt all other pending upgrades
            if upgrade_failed or broke_the_system:

                # display this if there are remaining apps
                if apps[number + 1 :]:
                    not_upgraded_apps = apps[number:]
                    logger.error(
                        m18n.n(
                            "app_not_upgraded",
                            failed_app=app_instance_name,
                            apps=", ".join(not_upgraded_apps),
                        )
                    )

                raise YunohostError(
                    failure_message_with_debug_instructions, raw_msg=True
                )

            # Otherwise we're good and keep going !
            now = int(time.time())
            app_setting(app_instance_name, "update_time", now)
            app_setting(
                app_instance_name,
                "current_revision",
                manifest.get("remote", {}).get("revision", "?"),
            )

            # Clean hooks and add new ones
            hook_remove(app_instance_name)
            if "hooks" in os.listdir(extracted_app_folder):
                for hook in os.listdir(extracted_app_folder + "/hooks"):
                    hook_add(app_instance_name, extracted_app_folder + "/hooks/" + hook)

            # Replace scripts and manifest and conf (if exists)
            # Move scripts and manifest to the right place
            for file_to_copy in APP_FILES_TO_COPY:
                rm(f"{app_setting_path}/{file_to_copy}", recursive=True, force=True)
                if os.path.exists(os.path.join(extracted_app_folder, file_to_copy)):
                    cp(
                        f"{extracted_app_folder}/{file_to_copy}",
                        f"{app_setting_path}/{file_to_copy}",
                        recursive=True,
                    )

            # Clean and set permissions
            shutil.rmtree(extracted_app_folder)
            chmod(app_setting_path, 0o600)
            chmod(f"{app_setting_path}/settings.yml", 0o400)
            chown(app_setting_path, "root", recursive=True)

            # So much win
            logger.success(m18n.n("app_upgraded", app=app_instance_name))

            hook_callback("post_app_upgrade", env=env_dict)
            operation_logger.success()

    permission_sync_to_user()

    logger.success(m18n.n("upgrade_complete"))


def app_manifest(app):

    manifest, extracted_app_folder = _extract_app(app)

    shutil.rmtree(extracted_app_folder)

    return manifest


@is_unit_operation()
def app_install(
    operation_logger,
    app,
    label=None,
    args=None,
    no_remove_on_failure=False,
    force=False,
):
    """
    Install apps

    Keyword argument:
        app -- Name, local path or git URL of the app to install
        label -- Custom name for the app
        args -- Serialize arguments for app installation
        no_remove_on_failure -- Debug option to avoid removing the app on a failed installation
        force -- Do not ask for confirmation when installing experimental / low-quality apps
    """

    from yunohost.hook import (
        hook_add,
        hook_remove,
        hook_callback,
        hook_exec,
        hook_exec_with_script_debug_if_failure,
    )
    from yunohost.log import OperationLogger
    from yunohost.permission import (
        user_permission_list,
        permission_create,
        permission_delete,
        permission_sync_to_user,
    )
    from yunohost.regenconf import manually_modified_files
    from yunohost.utils.legacy import _patch_legacy_php_versions, _patch_legacy_helpers

    # Check if disk space available
    if free_space_in_directory("/") <= 512 * 1000 * 1000:
        raise YunohostValidationError("disk_space_not_sufficient_install")

    def confirm_install(app):

        # Ignore if there's nothing for confirm (good quality app), if --force is used
        # or if request on the API (confirm already implemented on the API side)
        if force or Moulinette.interface.type == "api":
            return

        quality = _app_quality(app)
        if quality == "success":
            return

        # i18n: confirm_app_install_warning
        # i18n: confirm_app_install_danger
        # i18n: confirm_app_install_thirdparty

        if quality in ["danger", "thirdparty"]:
            answer = Moulinette.prompt(
                m18n.n("confirm_app_install_" + quality, answers="Yes, I understand"),
                color="red",
            )
            if answer != "Yes, I understand":
                raise YunohostError("aborting")

        else:
            answer = Moulinette.prompt(
                m18n.n("confirm_app_install_" + quality, answers="Y/N"), color="yellow"
            )
            if answer.upper() != "Y":
                raise YunohostError("aborting")

    confirm_install(app)
    manifest, extracted_app_folder = _extract_app(app)

    # Check ID
    if "id" not in manifest or "__" in manifest["id"] or "." in manifest["id"]:
        raise YunohostValidationError("app_id_invalid")

    app_id = manifest["id"]
    label = label if label else manifest["name"]

    # Check requirements
    _check_manifest_requirements(manifest)
    _assert_system_is_sane_for_app(manifest, "pre")

    # Check if app can be forked
    instance_number = _next_instance_number_for_app(app_id)
    if instance_number > 1:
        if "multi_instance" not in manifest or not is_true(manifest["multi_instance"]):
            raise YunohostValidationError("app_already_installed", app=app_id)

        # Change app_id to the forked app id
        app_instance_name = app_id + "__" + str(instance_number)
    else:
        app_instance_name = app_id

    # Retrieve arguments list for install script
    raw_questions = manifest.get("arguments", {}).get("install", {})
    questions = ask_questions_and_parse_answers(raw_questions, prefilled_answers=args)
    args = {
        question.name: question.value
        for question in questions
        if question.value is not None
    }

    # Validate domain / path availability for webapps
    path_requirement = _guess_webapp_path_requirement(extracted_app_folder)
    _validate_webpath_requirement(args, path_requirement)

    # Attempt to patch legacy helpers ...
    _patch_legacy_helpers(extracted_app_folder)

    # Apply dirty patch to make php5 apps compatible with php7
    _patch_legacy_php_versions(extracted_app_folder)

    # We'll check that the app didn't brutally edit some system configuration
    manually_modified_files_before_install = manually_modified_files()

    operation_logger.related_to = [
        s for s in operation_logger.related_to if s[0] != "app"
    ]
    operation_logger.related_to.append(("app", app_id))
    operation_logger.start()

    logger.info(m18n.n("app_start_install", app=app_id))

    # Create app directory
    app_setting_path = os.path.join(APPS_SETTING_PATH, app_instance_name)
    if os.path.exists(app_setting_path):
        shutil.rmtree(app_setting_path)
    os.makedirs(app_setting_path)

    # Set initial app settings
    app_settings = {
        "id": app_instance_name,
        "install_time": int(time.time()),
        "current_revision": manifest.get("remote", {}).get("revision", "?"),
    }
    _set_app_settings(app_instance_name, app_settings)

    # Move scripts and manifest to the right place
    for file_to_copy in APP_FILES_TO_COPY:
        if os.path.exists(os.path.join(extracted_app_folder, file_to_copy)):
            cp(
                f"{extracted_app_folder}/{file_to_copy}",
                f"{app_setting_path}/{file_to_copy}",
                recursive=True,
            )

    # Initialize the main permission for the app
    # The permission is initialized with no url associated, and with tile disabled
    # For web app, the root path of the app will be added as url and the tile
    # will be enabled during the app install. C.f. 'app_register_url()' below.
    permission_create(
        app_instance_name + ".main",
        allowed=["all_users"],
        label=label,
        show_tile=False,
        protected=False,
    )

    # Prepare env. var. to pass to script
    env_dict = _make_environment_for_app_script(
        app_instance_name, args=args, workdir=extracted_app_folder
    )

    env_dict_for_logging = env_dict.copy()
    for question in questions:
        # Or should it be more generally question.redact ?
        if question.type == "password":
            del env_dict_for_logging["YNH_APP_ARG_%s" % question.name.upper()]

    operation_logger.extra.update({"env": env_dict_for_logging})

    # Execute the app install script
    install_failed = True
    try:
        (
            install_failed,
            failure_message_with_debug_instructions,
        ) = hook_exec_with_script_debug_if_failure(
            os.path.join(extracted_app_folder, "scripts/install"),
            env=env_dict,
            operation_logger=operation_logger,
            error_message_if_script_failed=m18n.n("app_install_script_failed"),
            error_message_if_failed=lambda e: m18n.n(
                "app_install_failed", app=app_id, error=e
            ),
        )
    finally:
        # If success so far, validate that app didn't break important stuff
        if not install_failed:
            try:
                broke_the_system = False
                _assert_system_is_sane_for_app(manifest, "post")
            except Exception as e:
                broke_the_system = True
                logger.error(m18n.n("app_install_failed", app=app_id, error=str(e)))
                failure_message_with_debug_instructions = operation_logger.error(str(e))

        # We'll check that the app didn't brutally edit some system configuration
        manually_modified_files_after_install = manually_modified_files()
        manually_modified_files_by_app = set(
            manually_modified_files_after_install
        ) - set(manually_modified_files_before_install)
        if manually_modified_files_by_app:
            logger.error(
                "Packagers /!\\ This app manually modified some system configuration files! This should not happen! If you need to do so, you should implement a proper conf_regen hook. Those configuration were affected:\n    - "
                + "\n     -".join(manually_modified_files_by_app)
            )

        # If the install failed or broke the system, we remove it
        if install_failed or broke_the_system:

            # This option is meant for packagers to debug their apps more easily
            if no_remove_on_failure:
                raise YunohostError(
                    "The installation of %s failed, but was not cleaned up as requested by --no-remove-on-failure."
                    % app_id,
                    raw_msg=True,
                )
            else:
                logger.warning(m18n.n("app_remove_after_failed_install"))

            # Setup environment for remove script
            env_dict_remove = _make_environment_for_app_script(
                app_instance_name, workdir=extracted_app_folder
            )

            # Execute remove script
            operation_logger_remove = OperationLogger(
                "remove_on_failed_install",
                [("app", app_instance_name)],
                env=env_dict_remove,
            )
            operation_logger_remove.start()

            # Try to remove the app
            try:
                remove_retcode = hook_exec(
                    os.path.join(extracted_app_folder, "scripts/remove"),
                    args=[app_instance_name],
                    env=env_dict_remove,
                )[0]

            # Here again, calling hook_exec could fail miserably, or get
            # manually interrupted (by mistake or because script was stuck)
            # In that case we still want to proceed with the rest of the
            # removal (permissions, /etc/yunohost/apps/{app} ...)
            except (KeyboardInterrupt, EOFError, Exception):
                remove_retcode = -1
                import traceback

                logger.error(
                    m18n.n("unexpected_error", error="\n" + traceback.format_exc())
                )

            # Remove all permission in LDAP
            for permission_name in user_permission_list()["permissions"].keys():
                if permission_name.startswith(app_instance_name + "."):
                    permission_delete(permission_name, force=True, sync_perm=False)

            if remove_retcode != 0:
                msg = m18n.n("app_not_properly_removed", app=app_instance_name)
                logger.warning(msg)
                operation_logger_remove.error(msg)
            else:
                try:
                    _assert_system_is_sane_for_app(manifest, "post")
                except Exception as e:
                    operation_logger_remove.error(e)
                else:
                    operation_logger_remove.success()

            # Clean tmp folders
            shutil.rmtree(app_setting_path)
            shutil.rmtree(extracted_app_folder)

            permission_sync_to_user()

            raise YunohostError(failure_message_with_debug_instructions, raw_msg=True)

    # Clean hooks and add new ones
    hook_remove(app_instance_name)
    if "hooks" in os.listdir(extracted_app_folder):
        for file in os.listdir(extracted_app_folder + "/hooks"):
            hook_add(app_instance_name, extracted_app_folder + "/hooks/" + file)

    # Clean and set permissions
    shutil.rmtree(extracted_app_folder)
    chmod(app_setting_path, 0o600)
    chmod(f"{app_setting_path}/settings.yml", 0o400)
    chown(app_setting_path, "root", recursive=True)

    logger.success(m18n.n("installation_complete"))

    hook_callback("post_app_install", env=env_dict)


@is_unit_operation()
def app_remove(operation_logger, app, purge=False):
    """
    Remove app

    Keyword arguments:
        app -- App(s) to delete
        purge -- Remove with all app data

    """
    from yunohost.utils.legacy import _patch_legacy_php_versions, _patch_legacy_helpers
    from yunohost.hook import hook_exec, hook_remove, hook_callback
    from yunohost.permission import (
        user_permission_list,
        permission_delete,
        permission_sync_to_user,
    )

    if not _is_installed(app):
        raise YunohostValidationError(
            "app_not_installed", app=app, all_apps=_get_all_installed_apps_id()
        )

    operation_logger.start()

    logger.info(m18n.n("app_start_remove", app=app))

    app_setting_path = os.path.join(APPS_SETTING_PATH, app)

    # Attempt to patch legacy helpers ...
    _patch_legacy_helpers(app_setting_path)

    # Apply dirty patch to make php5 apps compatible with php7 (e.g. the remove
    # script might date back from jessie install)
    _patch_legacy_php_versions(app_setting_path)

    manifest = _get_manifest_of_app(app_setting_path)
    tmp_workdir_for_app = _make_tmp_workdir_for_app(app=app)
    remove_script = f"{tmp_workdir_for_app}/scripts/remove"

    env_dict = {}
    app_id, app_instance_nb = _parse_app_instance_name(app)
    env_dict = _make_environment_for_app_script(app, workdir=tmp_workdir_for_app)
    env_dict["YNH_APP_PURGE"] = str(1 if purge else 0)

    operation_logger.extra.update({"env": env_dict})
    operation_logger.flush()

    try:
        ret = hook_exec(remove_script, env=env_dict)[0]
    # Here again, calling hook_exec could fail miserably, or get
    # manually interrupted (by mistake or because script was stuck)
    # In that case we still want to proceed with the rest of the
    # removal (permissions, /etc/yunohost/apps/{app} ...)
    except (KeyboardInterrupt, EOFError, Exception):
        ret = -1
        import traceback

        logger.error(m18n.n("unexpected_error", error="\n" + traceback.format_exc()))
    finally:
        shutil.rmtree(tmp_workdir_for_app)

    if ret == 0:
        logger.success(m18n.n("app_removed", app=app))
        hook_callback("post_app_remove", env=env_dict)
    else:
        logger.warning(m18n.n("app_not_properly_removed", app=app))

    # Remove all permission in LDAP
    for permission_name in user_permission_list(apps=[app])["permissions"].keys():
        permission_delete(permission_name, force=True, sync_perm=False)

    if os.path.exists(app_setting_path):
        shutil.rmtree(app_setting_path)

    hook_remove(app)

    permission_sync_to_user()
    _assert_system_is_sane_for_app(manifest, "post")


def app_addaccess(apps, users=[]):
    """
    Grant access right to users (everyone by default)

    Keyword argument:
        users
        apps

    """
    from yunohost.permission import user_permission_update

    output = {}
    for app in apps:
        permission = user_permission_update(
            app + ".main", add=users, remove="all_users"
        )
        output[app] = permission["corresponding_users"]

    return {"allowed_users": output}


def app_removeaccess(apps, users=[]):
    """
    Revoke access right to users (everyone by default)

    Keyword argument:
        users
        apps

    """
    from yunohost.permission import user_permission_update

    output = {}
    for app in apps:
        permission = user_permission_update(app + ".main", remove=users)
        output[app] = permission["corresponding_users"]

    return {"allowed_users": output}


def app_clearaccess(apps):
    """
    Reset access rights for the app

    Keyword argument:
        apps

    """
    from yunohost.permission import user_permission_reset

    output = {}
    for app in apps:
        permission = user_permission_reset(app + ".main")
        output[app] = permission["corresponding_users"]

    return {"allowed_users": output}


@is_unit_operation()
def app_makedefault(operation_logger, app, domain=None):
    """
    Redirect domain root to an app

    Keyword argument:
        app
        domain

    """
    from yunohost.domain import _assert_domain_exists

    app_settings = _get_app_settings(app)
    app_domain = app_settings["domain"]
    app_path = app_settings["path"]

    if domain is None:
        domain = app_domain

    _assert_domain_exists(domain)

    operation_logger.related_to.append(("domain", domain))

    if "/" in app_map(raw=True)[domain]:
        raise YunohostValidationError(
            "app_make_default_location_already_used",
            app=app,
            domain=app_domain,
            other_app=app_map(raw=True)[domain]["/"]["id"],
        )

    operation_logger.start()

    # TODO / FIXME : current trick is to add this to conf.json.persisten
    # This is really not robust and should be improved
    # e.g. have a flag in /etc/yunohost/apps/$app/ to say that this is the
    # default app or idk...
    if not os.path.exists("/etc/ssowat/conf.json.persistent"):
        ssowat_conf = {}
    else:
        ssowat_conf = read_json("/etc/ssowat/conf.json.persistent")

    if "redirected_urls" not in ssowat_conf:
        ssowat_conf["redirected_urls"] = {}

    ssowat_conf["redirected_urls"][domain + "/"] = app_domain + app_path

    write_to_json(
        "/etc/ssowat/conf.json.persistent", ssowat_conf, sort_keys=True, indent=4
    )
    chmod("/etc/ssowat/conf.json.persistent", 0o644)

    logger.success(m18n.n("ssowat_conf_updated"))


def app_setting(app, key, value=None, delete=False):
    """
    Set or get an app setting value

    Keyword argument:
        value -- Value to set
        app -- App ID
        key -- Key to get/set
        delete -- Delete the key

    """
    app_settings = _get_app_settings(app) or {}

    #
    # Legacy permission setting management
    # (unprotected, protected, skipped_uri/regex)
    #

    is_legacy_permission_setting = any(
        key.startswith(word + "_") for word in ["unprotected", "protected", "skipped"]
    )

    if is_legacy_permission_setting:

        from yunohost.permission import (
            user_permission_list,
            user_permission_update,
            permission_create,
            permission_delete,
            permission_url,
        )

        permissions = user_permission_list(full=True, apps=[app])["permissions"]
        permission_name = "%s.legacy_%s_uris" % (app, key.split("_")[0])
        permission = permissions.get(permission_name)

        # GET
        if value is None and not delete:
            return (
                ",".join(permission.get("uris", []) + permission["additional_urls"])
                if permission
                else None
            )

        # DELETE
        if delete:
            # If 'is_public' setting still exists, we interpret this as
            # coming from a legacy app (because new apps shouldn't manage the
            # is_public state themselves anymore...)
            #
            # In that case, we interpret the request for "deleting
            # unprotected/skipped" setting as willing to make the app
            # private
            if (
                "is_public" in app_settings
                and "visitors" in permissions[app + ".main"]["allowed"]
            ):
                if key.startswith("unprotected_") or key.startswith("skipped_"):
                    user_permission_update(app + ".main", remove="visitors")

            if permission:
                permission_delete(permission_name)

        # SET
        else:

            urls = value
            # If the request is about the root of the app (/), ( = the vast majority of cases)
            # we interpret this as a change for the main permission
            # (i.e. allowing/disallowing visitors)
            if urls == "/":
                if key.startswith("unprotected_") or key.startswith("skipped_"):
                    permission_url(app + ".main", url="/", sync_perm=False)
                    user_permission_update(app + ".main", add="visitors")
                else:
                    user_permission_update(app + ".main", remove="visitors")
            else:

                urls = urls.split(",")
                if key.endswith("_regex"):
                    urls = ["re:" + url for url in urls]

                if permission:
                    # In case of new regex, save the urls, to add a new time in the additional_urls
                    # In case of new urls, we do the same thing but inversed
                    if key.endswith("_regex"):
                        # List of urls to save
                        current_urls_or_regex = [
                            url
                            for url in permission["additional_urls"]
                            if not url.startswith("re:")
                        ]
                    else:
                        # List of regex to save
                        current_urls_or_regex = [
                            url
                            for url in permission["additional_urls"]
                            if url.startswith("re:")
                        ]

                    new_urls = urls + current_urls_or_regex
                    # We need to clear urls because in the old setting the new setting override the old one and dont just add some urls
                    permission_url(permission_name, clear_urls=True, sync_perm=False)
                    permission_url(permission_name, add_url=new_urls)
                else:
                    from yunohost.utils.legacy import legacy_permission_label

                    # Let's create a "special" permission for the legacy settings
                    permission_create(
                        permission=permission_name,
                        # FIXME find a way to limit to only the user allowed to the main permission
                        allowed=["all_users"]
                        if key.startswith("protected_")
                        else ["all_users", "visitors"],
                        url=None,
                        additional_urls=urls,
                        auth_header=not key.startswith("skipped_"),
                        label=legacy_permission_label(app, key.split("_")[0]),
                        show_tile=False,
                        protected=True,
                    )

        return

    #
    # Regular setting management
    #

    # GET
    if value is None and not delete:
        return app_settings.get(key, None)

    # DELETE
    if delete:
        if key in app_settings:
            del app_settings[key]

    # SET
    else:
        if key in ["redirected_urls", "redirected_regex"]:
            value = yaml.safe_load(value)
        app_settings[key] = value

    _set_app_settings(app, app_settings)


def app_register_url(app, domain, path):
    """
    Book/register a web path for a given app

    Keyword argument:
        app -- App which will use the web path
        domain -- The domain on which the app should be registered (e.g. your.domain.tld)
        path -- The path to be registered (e.g. /coffee)
    """
    from yunohost.permission import (
        permission_url,
        user_permission_update,
        permission_sync_to_user,
    )

    domain = DomainQuestion.normalize(domain)
    path = PathQuestion.normalize(path)

    # We cannot change the url of an app already installed simply by changing
    # the settings...

    if _is_installed(app):
        settings = _get_app_settings(app)
        if "path" in settings.keys() and "domain" in settings.keys():
            raise YunohostValidationError("app_already_installed_cant_change_url")

    # Check the url is available
    _assert_no_conflicting_apps(domain, path)

    app_setting(app, "domain", value=domain)
    app_setting(app, "path", value=path)

    # Initially, the .main permission is created with no url at all associated
    # When the app register/books its web url, we also add the url '/'
    # (meaning the root of the app, domain.tld/path/)
    # and enable the tile to the SSO, and both of this should match 95% of apps
    # For more specific cases, the app is free to change / add urls or disable
    # the tile using the permission helpers.
    permission_url(app + ".main", url="/", sync_perm=False)
    user_permission_update(app + ".main", show_tile=True, sync_perm=False)
    permission_sync_to_user()


def app_ssowatconf():
    """
    Regenerate SSOwat configuration file


    """
    from yunohost.domain import domain_list, _get_maindomain
    from yunohost.permission import user_permission_list

    main_domain = _get_maindomain()
    domains = domain_list()["domains"]
    all_permissions = user_permission_list(
        full=True, ignore_system_perms=True, absolute_urls=True
    )["permissions"]

    permissions = {
        "core_skipped": {
            "users": [],
            "label": "Core permissions - skipped",
            "show_tile": False,
            "auth_header": False,
            "public": True,
            "uris": [domain + "/yunohost/admin" for domain in domains]
            + [domain + "/yunohost/api" for domain in domains]
            + [
                "re:^[^/]*/%.well%-known/ynh%-diagnosis/.*$",
                "re:^[^/]*/%.well%-known/acme%-challenge/.*$",
                "re:^[^/]*/%.well%-known/autoconfig/mail/config%-v1%.1%.xml.*$",
            ],
        }
    }
    redirected_regex = {
        main_domain + r"/yunohost[\/]?$": "https://" + main_domain + "/yunohost/sso/"
    }
    redirected_urls = {}

    for app in _installed_apps():

        app_settings = read_yaml(APPS_SETTING_PATH + app + "/settings.yml")

        # Redirected
        redirected_urls.update(app_settings.get("redirected_urls", {}))
        redirected_regex.update(app_settings.get("redirected_regex", {}))

    # New permission system
    for perm_name, perm_info in all_permissions.items():

        uris = (
            []
            + ([perm_info["url"]] if perm_info["url"] else [])
            + perm_info["additional_urls"]
        )

        # Ignore permissions for which there's no url defined
        if not uris:
            continue

        permissions[perm_name] = {
            "users": perm_info["corresponding_users"],
            "label": perm_info["label"],
            "show_tile": perm_info["show_tile"]
            and perm_info["url"]
            and (not perm_info["url"].startswith("re:")),
            "auth_header": perm_info["auth_header"],
            "public": "visitors" in perm_info["allowed"],
            "uris": uris,
        }

    conf_dict = {
        "portal_domain": main_domain,
        "portal_path": "/yunohost/sso/",
        "additional_headers": {
            "Auth-User": "uid",
            "Remote-User": "uid",
            "Name": "cn",
            "Email": "mail",
        },
        "domains": domains,
        "redirected_urls": redirected_urls,
        "redirected_regex": redirected_regex,
        "permissions": permissions,
    }

    write_to_json("/etc/ssowat/conf.json", conf_dict, sort_keys=True, indent=4)

    from .utils.legacy import translate_legacy_rules_in_ssowant_conf_json_persistent

    translate_legacy_rules_in_ssowant_conf_json_persistent()

    logger.debug(m18n.n("ssowat_conf_generated"))


def app_change_label(app, new_label):
    from yunohost.permission import user_permission_update

    installed = _is_installed(app)
    if not installed:
        raise YunohostValidationError(
            "app_not_installed", app=app, all_apps=_get_all_installed_apps_id()
        )
    logger.warning(m18n.n("app_label_deprecated"))
    user_permission_update(app + ".main", label=new_label)


# actions todo list:
# * docstring


def app_action_list(app):
    logger.warning(m18n.n("experimental_feature"))

    # this will take care of checking if the app is installed
    app_info_dict = app_info(app)

    return {
        "app": app,
        "app_name": app_info_dict["name"],
        "actions": _get_app_actions(app),
    }


@is_unit_operation()
def app_action_run(operation_logger, app, action, args=None):
    logger.warning(m18n.n("experimental_feature"))

    from yunohost.hook import hook_exec

    # will raise if action doesn't exist
    actions = app_action_list(app)["actions"]
    actions = {x["id"]: x for x in actions}

    if action not in actions:
        raise YunohostValidationError(
            "action '%s' not available for app '%s', available actions are: %s"
            % (action, app, ", ".join(actions.keys())),
            raw_msg=True,
        )

    operation_logger.start()

    action_declaration = actions[action]

    # Retrieve arguments list for install script
    raw_questions = actions[action].get("arguments", {})
    questions = ask_questions_and_parse_answers(raw_questions, prefilled_answers=args)
    args = {
        question.name: question.value
        for question in questions
        if question.value is not None
    }

    tmp_workdir_for_app = _make_tmp_workdir_for_app(app=app)

    env_dict = _make_environment_for_app_script(
        app, args=args, args_prefix="ACTION_", workdir=tmp_workdir_for_app
    )
    env_dict["YNH_ACTION"] = action

    _, action_script = tempfile.mkstemp(dir=tmp_workdir_for_app)

    with open(action_script, "w") as script:
        script.write(action_declaration["command"])

    if action_declaration.get("cwd"):
        cwd = action_declaration["cwd"].replace("$app", app)
    else:
        cwd = tmp_workdir_for_app

    try:
        retcode = hook_exec(
            action_script,
            env=env_dict,
            chdir=cwd,
            user=action_declaration.get("user", "root"),
        )[0]
    # Calling hook_exec could fail miserably, or get
    # manually interrupted (by mistake or because script was stuck)
    # In that case we still want to delete the tmp work dir
    except (KeyboardInterrupt, EOFError, Exception):
        retcode = -1
        import traceback

        logger.error(m18n.n("unexpected_error", error="\n" + traceback.format_exc()))
    finally:
        shutil.rmtree(tmp_workdir_for_app)

    if retcode not in action_declaration.get("accepted_return_codes", [0]):
        msg = "Error while executing action '%s' of app '%s': return code %s" % (
            action,
            app,
            retcode,
        )
        operation_logger.error(msg)
        raise YunohostError(msg, raw_msg=True)

    operation_logger.success()
    return logger.success("Action successed!")


def app_config_get(app, key="", full=False, export=False):
    """
    Display an app configuration in classic, full or export mode
    """
    if full and export:
        raise YunohostValidationError(
            "You can't use --full and --export together.", raw_msg=True
        )

    if full:
        mode = "full"
    elif export:
        mode = "export"
    else:
        mode = "classic"

    config_ = AppConfigPanel(app)
    return config_.get(key, mode)


@is_unit_operation()
def app_config_set(
    operation_logger, app, key=None, value=None, args=None, args_file=None
):
    """
    Apply a new app configuration
    """

    config_ = AppConfigPanel(app)

    return config_.set(key, value, args, args_file, operation_logger=operation_logger)


class AppConfigPanel(ConfigPanel):
    def __init__(self, app):

        # Check app is installed
        _assert_is_installed(app)

        self.app = app
        config_path = os.path.join(APPS_SETTING_PATH, app, "config_panel.toml")
        super().__init__(config_path=config_path)

    def _load_current_values(self):
        self.values = self._call_config_script("show")

    def _apply(self):
        env = {key: str(value) for key, value in self.new_values.items()}
        return_content = self._call_config_script("apply", env=env)

        # If the script returned validation error
        # raise a ValidationError exception using
        # the first key
        if return_content:
            for key, message in return_content.get("validation_errors").items():
                raise YunohostValidationError(
                    "app_argument_invalid",
                    name=key,
                    error=message,
                )

    def _call_config_script(self, action, env={}):
        from yunohost.hook import hook_exec

        # Add default config script if needed
        config_script = os.path.join(APPS_SETTING_PATH, self.app, "scripts", "config")
        if not os.path.exists(config_script):
            logger.debug("Adding a default config script")
            default_script = """#!/bin/bash
source /usr/share/yunohost/helpers
ynh_abort_if_errors
ynh_app_config_run $1
"""
            write_to_file(config_script, default_script)

        # Call config script to extract current values
        logger.debug(f"Calling '{action}' action from config script")
        app_id, app_instance_nb = _parse_app_instance_name(self.app)
        settings = _get_app_settings(app_id)
        env.update(
            {
                "app_id": app_id,
                "app": self.app,
                "app_instance_nb": str(app_instance_nb),
                "final_path": settings.get("final_path", ""),
                "YNH_APP_BASEDIR": os.path.join(APPS_SETTING_PATH, self.app),
            }
        )

        ret, values = hook_exec(config_script, args=[action], env=env)
        if ret != 0:
            if action == "show":
                raise YunohostError("app_config_unable_to_read")
            else:
                raise YunohostError("app_config_unable_to_apply")
        return values


def _get_app_actions(app_id):
    "Get app config panel stored in json or in toml"
    actions_toml_path = os.path.join(APPS_SETTING_PATH, app_id, "actions.toml")
    actions_json_path = os.path.join(APPS_SETTING_PATH, app_id, "actions.json")

    # sample data to get an idea of what is going on
    # this toml extract:
    #

    # [restart_service]
    # name = "Restart service"
    # command = "echo pouet $YNH_ACTION_SERVICE"
    # user = "root"  # optional
    # cwd = "/" # optional
    # accepted_return_codes = [0, 1, 2, 3]  # optional
    # description.en = "a dummy stupid exemple or restarting a service"
    #
    #     [restart_service.arguments.service]
    #     type = "string",
    #     ask.en = "service to restart"
    #     example = "nginx"
    #
    # will be parsed into this:
    #
    # OrderedDict([(u'restart_service',
    #               OrderedDict([(u'name', u'Restart service'),
    #                            (u'command', u'echo pouet $YNH_ACTION_SERVICE'),
    #                            (u'user', u'root'),
    #                            (u'cwd', u'/'),
    #                            (u'accepted_return_codes', [0, 1, 2, 3]),
    #                            (u'description',
    #                             OrderedDict([(u'en',
    #                                           u'a dummy stupid exemple or restarting a service')])),
    #                            (u'arguments',
    #                             OrderedDict([(u'service',
    #                                           OrderedDict([(u'type', u'string'),
    #                                                        (u'ask',
    #                                                         OrderedDict([(u'en',
    #                                                                       u'service to restart')])),
    #                                                        (u'example',
    #                                                         u'nginx')]))]))])),
    #
    #
    # and needs to be converted into this:
    #
    # [{u'accepted_return_codes': [0, 1, 2, 3],
    #   u'arguments': [{u'ask': {u'en': u'service to restart'},
    #     u'example': u'nginx',
    #     u'name': u'service',
    #     u'type': u'string'}],
    #   u'command': u'echo pouet $YNH_ACTION_SERVICE',
    #   u'cwd': u'/',
    #   u'description': {u'en': u'a dummy stupid exemple or restarting a service'},
    #   u'id': u'restart_service',
    #   u'name': u'Restart service',
    #   u'user': u'root'}]

    if os.path.exists(actions_toml_path):
        toml_actions = toml.load(open(actions_toml_path, "r"), _dict=OrderedDict)

        # transform toml format into json format
        actions = []

        for key, value in toml_actions.items():
            action = dict(**value)
            action["id"] = key

            arguments = []
            for argument_name, argument in value.get("arguments", {}).items():
                argument = dict(**argument)
                argument["name"] = argument_name

                arguments.append(argument)

            action["arguments"] = arguments
            actions.append(action)

        return actions

    elif os.path.exists(actions_json_path):
        return json.load(open(actions_json_path))

    return None


def _get_app_settings(app_id):
    """
    Get settings of an installed app

    Keyword arguments:
        app_id -- The app id

    """
    if not _is_installed(app_id):
        raise YunohostValidationError(
            "app_not_installed", app=app_id, all_apps=_get_all_installed_apps_id()
        )
    try:
        with open(os.path.join(APPS_SETTING_PATH, app_id, "settings.yml")) as f:
            settings = yaml.safe_load(f)
        # If label contains unicode char, this may later trigger issues when building strings...
        # FIXME: this should be propagated to read_yaml so that this fix applies everywhere I think...
        settings = {k: v for k, v in settings.items()}

        # Stupid fix for legacy bullshit
        # In the past, some setups did not have proper normalization for app domain/path
        # Meaning some setups (as of January 2021) still have path=/foobar/ (with a trailing slash)
        # resulting in stupid issue unless apps using ynh_app_normalize_path_stuff
        # So we yolofix the settings if such an issue is found >_>
        # A simple call  to `yunohost app list` (which happens quite often) should be enough
        # to migrate all app settings ... so this can probably be removed once we're past Bullseye...
        if settings.get("path") != "/" and (
            settings.get("path", "").endswith("/")
            or not settings.get("path", "/").startswith("/")
        ):
            settings["path"] = "/" + settings["path"].strip("/")
            _set_app_settings(app_id, settings)

        if app_id == settings["id"]:
            return settings
    except (IOError, TypeError, KeyError):
        logger.error(m18n.n("app_not_correctly_installed", app=app_id))
    return {}


def _set_app_settings(app_id, settings):
    """
    Set settings of an app

    Keyword arguments:
        app_id -- The app id
        settings -- Dict with app settings

    """
    with open(os.path.join(APPS_SETTING_PATH, app_id, "settings.yml"), "w") as f:
        yaml.safe_dump(settings, f, default_flow_style=False)


def _get_manifest_of_app(path):
    "Get app manifest stored in json or in toml"

    # sample data to get an idea of what is going on
    # this toml extract:
    #
    # license = "free"
    # url = "https://example.com"
    # multi_instance = true
    # version = "1.0~ynh1"
    # packaging_format = 1
    # services = ["nginx", "php7.0-fpm", "mysql"]
    # id = "ynhexample"
    # name = "YunoHost example app"
    #
    # [requirements]
    # yunohost = ">= 3.5"
    #
    # [maintainer]
    # url = "http://example.com"
    # name = "John doe"
    # email = "john.doe@example.com"
    #
    # [description]
    # fr = "Exemple de package d'application pour YunoHost."
    # en = "Example package for YunoHost application."
    #
    # [arguments]
    #     [arguments.install.domain]
    #     type = "domain"
    #     example = "example.com"
    #         [arguments.install.domain.ask]
    #         fr = "Choisissez un nom de domaine pour ynhexample"
    #         en = "Choose a domain name for ynhexample"
    #
    # will be parsed into this:
    #
    # OrderedDict([(u'license', u'free'),
    #              (u'url', u'https://example.com'),
    #              (u'multi_instance', True),
    #              (u'version', u'1.0~ynh1'),
    #              (u'packaging_format', 1),
    #              (u'services', [u'nginx', u'php7.0-fpm', u'mysql']),
    #              (u'id', u'ynhexample'),
    #              (u'name', u'YunoHost example app'),
    #              (u'requirements', OrderedDict([(u'yunohost', u'>= 3.5')])),
    #              (u'maintainer',
    #               OrderedDict([(u'url', u'http://example.com'),
    #                            (u'name', u'John doe'),
    #                            (u'email', u'john.doe@example.com')])),
    #              (u'description',
    #               OrderedDict([(u'fr',
    #                             u"Exemple de package d'application pour YunoHost."),
    #                            (u'en',
    #                             u'Example package for YunoHost application.')])),
    #              (u'arguments',
    #               OrderedDict([(u'install',
    #                             OrderedDict([(u'domain',
    #                                           OrderedDict([(u'type', u'domain'),
    #                                                        (u'example',
    #                                                         u'example.com'),
    #                                                        (u'ask',
    #                                                         OrderedDict([(u'fr',
    #                                                                       u'Choisissez un nom de domaine pour ynhexample'),
    #                                                                      (u'en',
    #                                                                       u'Choose a domain name for ynhexample')]))])),
    #
    # and needs to be converted into this:
    #
    # {
    #     "name": "YunoHost example app",
    #     "id": "ynhexample",
    #     "packaging_format": 1,
    #     "description": {
    #     ¦   "en": "Example package for YunoHost application.",
    #     ¦   "fr": "Exemple de package d’application pour YunoHost."
    #     },
    #     "version": "1.0~ynh1",
    #     "url": "https://example.com",
    #     "license": "free",
    #     "maintainer": {
    #     ¦   "name": "John doe",
    #     ¦   "email": "john.doe@example.com",
    #     ¦   "url": "http://example.com"
    #     },
    #     "requirements": {
    #     ¦   "yunohost": ">= 3.5"
    #     },
    #     "multi_instance": true,
    #     "services": [
    #     ¦   "nginx",
    #     ¦   "php7.0-fpm",
    #     ¦   "mysql"
    #     ],
    #     "arguments": {
    #     ¦   "install" : [
    #     ¦   ¦   {
    #     ¦   ¦   ¦   "name": "domain",
    #     ¦   ¦   ¦   "type": "domain",
    #     ¦   ¦   ¦   "ask": {
    #     ¦   ¦   ¦   ¦   "en": "Choose a domain name for ynhexample",
    #     ¦   ¦   ¦   ¦   "fr": "Choisissez un nom de domaine pour ynhexample"
    #     ¦   ¦   ¦   },
    #     ¦   ¦   ¦   "example": "example.com"
    #     ¦   ¦   },

    if os.path.exists(os.path.join(path, "manifest.toml")):
        manifest_toml = read_toml(os.path.join(path, "manifest.toml"))

        manifest = manifest_toml.copy()

        install_arguments = []
        for name, values in (
            manifest_toml.get("arguments", {}).get("install", {}).items()
        ):
            args = values.copy()
            args["name"] = name

            install_arguments.append(args)

        manifest["arguments"]["install"] = install_arguments

    elif os.path.exists(os.path.join(path, "manifest.json")):
        manifest = read_json(os.path.join(path, "manifest.json"))
    else:
        raise YunohostError(
            "There doesn't seem to be any manifest file in %s ... It looks like an app was not correctly installed/removed."
            % path,
            raw_msg=True,
        )

    manifest["arguments"] = _set_default_ask_questions(manifest.get("arguments", {}))
    return manifest


def _set_default_ask_questions(arguments):

    # arguments is something like
    # { "install": [
    #       { "name": "domain",
    #         "type": "domain",
    #         ....
    #       },
    #       { "name": "path",
    #         "type": "path"
    #         ...
    #       },
    #       ...
    #   ],
    #  "upgrade": [ ... ]
    # }

    # We set a default for any question with these matching (type, name)
    #                           type       namei
    # N.B. : this is only for install script ... should be reworked for other
    # scripts if we supports args for other scripts in the future...
    questions_with_default = [
        ("domain", "domain"),  # i18n: app_manifest_install_ask_domain
        ("path", "path"),  # i18n: app_manifest_install_ask_path
        ("password", "password"),  # i18n: app_manifest_install_ask_password
        ("user", "admin"),  # i18n: app_manifest_install_ask_admin
        ("boolean", "is_public"),
    ]  # i18n: app_manifest_install_ask_is_public

    for script_name, arg_list in arguments.items():

        # We only support questions for install so far, and for other
        if script_name != "install":
            continue

        for arg in arg_list:

            # Do not override 'ask' field if provided by app ?... Or shall we ?
            # if "ask" in arg:
            #    continue

            # If this arg corresponds to a question with default ask message...
            if any(
                (arg.get("type"), arg["name"]) == question
                for question in questions_with_default
            ):
                # The key is for example "app_manifest_install_ask_domain"
                key = "app_manifest_%s_ask_%s" % (script_name, arg["name"])
                arg["ask"] = m18n.n(key)

            # Also it in fact doesn't make sense for any of those questions to have an example value nor a default value...
            if arg.get("type") in ["domain", "user", "password"]:
                if "example" in arg:
                    del arg["example"]
                if "default" in arg:
                    del arg["domain"]

    return arguments


def _is_app_repo_url(string: str) -> bool:

    string = string.strip()

    # Dummy test for ssh-based stuff ... should probably be improved somehow
    if "@" in string:
        return True

    return bool(APP_REPO_URL.match(string))


def _app_quality(src: str) -> str:
    """
    app may in fact be an app name, an url, or a path
    """

    raw_app_catalog = _load_apps_catalog()["apps"]
    if src in raw_app_catalog or _is_app_repo_url(src):

        # If we got an app name directly (e.g. just "wordpress"), we gonna test this name
        if src in raw_app_catalog:
            app_name_to_test = src
        # If we got an url like "https://github.com/foo/bar_ynh, we want to
        # extract "bar" and test if we know this app
        elif ("http://" in src) or ("https://" in src):
            app_name_to_test = src.strip("/").split("/")[-1].replace("_ynh", "")
        else:
            # FIXME : watdo if '@' in app ?
            return "thirdparty"

        if app_name_to_test in raw_app_catalog:

            state = raw_app_catalog[app_name_to_test].get("state", "notworking")
            level = raw_app_catalog[app_name_to_test].get("level", None)
            if state in ["working", "validated"]:
                if isinstance(level, int) and level >= 5:
                    return "success"
                elif isinstance(level, int) and level > 0:
                    return "warning"
            return "danger"
        else:
            return "thirdparty"

    elif os.path.exists(src):
        return "thirdparty"
    else:
        if "http://" in src or "https://" in src:
            logger.error(
                f"{src} is not a valid app url: app url are expected to look like https://domain.tld/path/to/repo_ynh"
            )
        raise YunohostValidationError("app_unknown")


def _extract_app(src: str) -> Tuple[Dict, str]:
    """
    src may be an app name, an url, or a path
    """

    raw_app_catalog = _load_apps_catalog()["apps"]

    # App is an appname in the catalog
    if src in raw_app_catalog:
        if "git" not in raw_app_catalog[src]:
            raise YunohostValidationError("app_unsupported_remote_type")

        app_info = raw_app_catalog[src]
        url = app_info["git"]["url"]
        branch = app_info["git"]["branch"]
        revision = str(app_info["git"]["revision"])
        return _extract_app_from_gitrepo(url, branch, revision, app_info)
    # App is a git repo url
    elif _is_app_repo_url(src):
        url = src.strip().strip("/")
        branch = "master"
        revision = "HEAD"
        # gitlab urls may look like 'https://domain/org/group/repo/-/tree/testing'
        # compated to github urls looking like 'https://domain/org/repo/tree/testing'
        if "/-/" in url:
            url = url.replace("/-/", "/")
        if "/tree/" in url:
            url, branch = url.split("/tree/", 1)
        return _extract_app_from_gitrepo(url, branch, revision, {})
    # App is a local folder
    elif os.path.exists(src):
        return _extract_app_from_folder(src)
    else:
        if "http://" in src or "https://" in src:
            logger.error(
                f"{src} is not a valid app url: app url are expected to look like https://domain.tld/path/to/repo_ynh"
            )
        raise YunohostValidationError("app_unknown")


def _extract_app_from_folder(path: str) -> Tuple[Dict, str]:
    """
    Unzip / untar / copy application tarball or directory to a tmp work directory

    Keyword arguments:
        path -- Path of the tarball or directory
    """
    logger.debug(m18n.n("extracting"))

    path = os.path.abspath(path)

    extracted_app_folder = _make_tmp_workdir_for_app()

    if os.path.isdir(path):
        shutil.rmtree(extracted_app_folder)
        if path[-1] != "/":
            path = path + "/"
        cp(path, extracted_app_folder, recursive=True)
    else:
        try:
            shutil.unpack_archive(path, extracted_app_folder)
        except Exception:
            raise YunohostError("app_extraction_failed")

    try:
        if len(os.listdir(extracted_app_folder)) == 1:
            for folder in os.listdir(extracted_app_folder):
                extracted_app_folder = extracted_app_folder + "/" + folder
    except IOError:
        raise YunohostError("app_install_files_invalid")

    manifest = _get_manifest_of_app(extracted_app_folder)
    manifest["lastUpdate"] = int(time.time())

    logger.debug(m18n.n("done"))

    manifest["remote"] = {"type": "file", "path": path}
    return manifest, extracted_app_folder


def _extract_app_from_gitrepo(
    url: str, branch: str, revision: str, app_info: Dict = {}
) -> Tuple[Dict, str]:

    logger.debug(m18n.n("downloading"))

    extracted_app_folder = _make_tmp_workdir_for_app()

    # Download only this commit
    try:
        # We don't use git clone because, git clone can't download
        # a specific revision only
        ref = branch if revision == "HEAD" else revision
        run_commands([["git", "init", extracted_app_folder]], shell=False)
        run_commands(
            [
                ["git", "remote", "add", "origin", url],
                ["git", "fetch", "--depth=1", "origin", ref],
                ["git", "reset", "--hard", "FETCH_HEAD"],
            ],
            cwd=extracted_app_folder,
            shell=False,
        )
    except subprocess.CalledProcessError:
        raise YunohostError("app_sources_fetch_failed")
    else:
        logger.debug(m18n.n("done"))

    manifest = _get_manifest_of_app(extracted_app_folder)

    # Store remote repository info into the returned manifest
    manifest["remote"] = {"type": "git", "url": url, "branch": branch}
    if revision == "HEAD":
        try:
            # Get git last commit hash
            cmd = f"git ls-remote --exit-code {url} {branch} | awk '{{print $1}}'"
            manifest["remote"]["revision"] = check_output(cmd)
        except Exception as e:
            logger.warning("cannot get last commit hash because: %s ", e)
    else:
        manifest["remote"]["revision"] = revision
        manifest["lastUpdate"] = app_info.get("lastUpdate")

    return manifest, extracted_app_folder


#
# ############################### #
#        Small utilities          #
# ############################### #
#


def _is_installed(app: str) -> bool:
    """
    Check if application is installed

    Keyword arguments:
        app -- id of App to check

    Returns:
        Boolean

    """
    return os.path.isdir(APPS_SETTING_PATH + app)


def _assert_is_installed(app: str) -> None:
    if not _is_installed(app):
        raise YunohostValidationError(
            "app_not_installed", app=app, all_apps=_get_all_installed_apps_id()
        )


def _installed_apps() -> List[str]:
    return os.listdir(APPS_SETTING_PATH)


def _get_all_installed_apps_id():
    """
    Return something like:
       ' * app1
         * app2
         * ...'
    """

    all_apps_ids = sorted(_installed_apps())

    all_apps_ids_formatted = "\n * ".join(all_apps_ids)
    all_apps_ids_formatted = "\n * " + all_apps_ids_formatted

    return all_apps_ids_formatted


def _check_manifest_requirements(manifest: Dict):
    """Check if required packages are met from the manifest"""

    packaging_format = int(manifest.get("packaging_format", 0))
    if packaging_format not in [0, 1]:
        raise YunohostValidationError("app_packaging_format_not_supported")

    requirements = manifest.get("requirements", dict())

    if not requirements:
        return

    app = manifest.get("id", "?")

    logger.debug(m18n.n("app_requirements_checking", app=app))

    # Iterate over requirements
    for pkgname, spec in requirements.items():
        if not packages.meets_version_specifier(pkgname, spec):
            version = packages.ynh_packages_version()[pkgname]["version"]
            raise YunohostValidationError(
                "app_requirements_unmeet",
                pkgname=pkgname,
                version=version,
                spec=spec,
                app=app,
            )


def _guess_webapp_path_requirement(app_folder: str) -> str:

    # If there's only one "domain" and "path", validate that domain/path
    # is an available url and normalize the path.

    manifest = _get_manifest_of_app(app_folder)
    raw_questions = manifest.get("arguments", {}).get("install", {})

    domain_questions = [
        question for question in raw_questions if question.get("type") == "domain"
    ]
    path_questions = [
        question for question in raw_questions if question.get("type") == "path"
    ]

    if len(domain_questions) == 0 and len(path_questions) == 0:
        return ""
    if len(domain_questions) == 1 and len(path_questions) == 1:
        return "domain_and_path"
    if len(domain_questions) == 1 and len(path_questions) == 0:
        # This is likely to be a full-domain app...

        # Confirm that this is a full-domain app This should cover most cases
        # ...  though anyway the proper solution is to implement some mechanism
        # in the manifest for app to declare that they require a full domain
        # (among other thing) so that we can dynamically check/display this
        # requirement on the webadmin form and not miserably fail at submit time

        # Full-domain apps typically declare something like path_url="/" or path=/
        # and use ynh_webpath_register or yunohost_app_checkurl inside the install script
        install_script_content = read_file(os.path.join(app_folder, "scripts/install"))

        if re.search(
            r"\npath(_url)?=[\"']?/[\"']?", install_script_content
        ) and re.search(r"ynh_webpath_register", install_script_content):
            return "full_domain"

    return "?"


def _validate_webpath_requirement(
    args: Dict[str, Any], path_requirement: str, ignore_app=None
) -> None:

    domain = args.get("domain")
    path = args.get("path")

    if path_requirement == "domain_and_path":
        _assert_no_conflicting_apps(domain, path, ignore_app=ignore_app)

    elif path_requirement == "full_domain":
        _assert_no_conflicting_apps(
            domain, "/", full_domain=True, ignore_app=ignore_app
        )


def _get_conflicting_apps(domain, path, ignore_app=None):
    """
    Return a list of all conflicting apps with a domain/path (it can be empty)

    Keyword argument:
        domain -- The domain for the web path (e.g. your.domain.tld)
        path -- The path to check (e.g. /coffee)
        ignore_app -- An optional app id to ignore (c.f. the change_url usecase)
    """

    from yunohost.domain import _assert_domain_exists

    domain = DomainQuestion.normalize(domain)
    path = PathQuestion.normalize(path)

    # Abort if domain is unknown
    _assert_domain_exists(domain)

    # Fetch apps map
    apps_map = app_map(raw=True)

    # Loop through all apps to check if path is taken by one of them
    conflicts = []
    if domain in apps_map:
        # Loop through apps
        for p, a in apps_map[domain].items():
            if a["id"] == ignore_app:
                continue
            if path == p:
                conflicts.append((p, a["id"], a["label"]))
            # We also don't want conflicts with other apps starting with
            # same name
            elif path.startswith(p) or p.startswith(path):
                conflicts.append((p, a["id"], a["label"]))

    return conflicts


def _assert_no_conflicting_apps(domain, path, ignore_app=None, full_domain=False):

    conflicts = _get_conflicting_apps(domain, path, ignore_app)

    if conflicts:
        apps = []
        for path, app_id, app_label in conflicts:
            apps.append(
                " * {domain:s}{path:s} → {app_label:s} ({app_id:s})".format(
                    domain=domain,
                    path=path,
                    app_id=app_id,
                    app_label=app_label,
                )
            )

        if full_domain:
            raise YunohostValidationError("app_full_domain_unavailable", domain=domain)
        else:
            raise YunohostValidationError(
                "app_location_unavailable", apps="\n".join(apps)
            )


def _make_environment_for_app_script(
    app, args={}, args_prefix="APP_ARG_", workdir=None
):

    app_setting_path = os.path.join(APPS_SETTING_PATH, app)

    manifest = _get_manifest_of_app(app_setting_path)
    app_id, app_instance_nb = _parse_app_instance_name(app)

    env_dict = {
        "YNH_APP_ID": app_id,
        "YNH_APP_INSTANCE_NAME": app,
        "YNH_APP_INSTANCE_NUMBER": str(app_instance_nb),
        "YNH_APP_MANIFEST_VERSION": manifest.get("version", "?"),
    }

    if workdir:
        env_dict["YNH_APP_BASEDIR"] = workdir

    for arg_name, arg_value in args.items():
        env_dict["YNH_%s%s" % (args_prefix, arg_name.upper())] = str(arg_value)

    return env_dict


def _parse_app_instance_name(app_instance_name: str) -> Tuple[str, int]:
    """
    Parse a Yunohost app instance name and extracts the original appid
    and the application instance number

    'yolo'      -> ('yolo', 1)
    'yolo1'     -> ('yolo1', 1)
    'yolo__0'   -> ('yolo__0', 1)
    'yolo__1'   -> ('yolo', 1)
    'yolo__23'  -> ('yolo', 23)
    'yolo__42__72'    -> ('yolo__42', 72)
    'yolo__23qdqsd'   -> ('yolo__23qdqsd', 1)
    'yolo__23qdqsd56' -> ('yolo__23qdqsd56', 1)
    """
    match = re_app_instance_name.match(app_instance_name)
    assert match, f"Could not parse app instance name : {app_instance_name}"
    appid = match.groupdict().get("appid")
    app_instance_nb_ = match.groupdict().get("appinstancenb") or "1"
    if not appid:
        raise Exception(f"Could not parse app instance name : {app_instance_name}")
    if not str(app_instance_nb_).isdigit():
        raise Exception(f"Could not parse app instance name : {app_instance_name}")
    else:
        app_instance_nb = int(str(app_instance_nb_))

    return (appid, app_instance_nb)


def _next_instance_number_for_app(app):

    # Get list of sibling apps, such as {app}, {app}__2, {app}__4
    apps = _installed_apps()
    sibling_app_ids = [a for a in apps if a == app or a.startswith(f"{app}__")]

    # Find the list of ids, such as [1, 2, 4]
    sibling_ids = [_parse_app_instance_name(a)[1] for a in sibling_app_ids]

    # Find the first 'i' that's not in the sibling_ids list already
    i = 1
    while True:
        if i not in sibling_ids:
            return i
        else:
            i += 1


def _make_tmp_workdir_for_app(app=None):

    # Create parent dir if it doesn't exists yet
    if not os.path.exists(APP_TMP_WORKDIRS):
        os.makedirs(APP_TMP_WORKDIRS)

    now = int(time.time())

    # Cleanup old dirs (if any)
    for dir_ in os.listdir(APP_TMP_WORKDIRS):
        path = os.path.join(APP_TMP_WORKDIRS, dir_)
        # We only delete folders older than an arbitary 12 hours
        # This is to cover the stupid case of upgrades
        # Where many app will call 'yunohost backup create'
        # from the upgrade script itself,
        # which will also call this function while the upgrade
        # script itself is running in one of those dir...
        # It could be that there are other edge cases
        # such as app-install-during-app-install
        if os.stat(path).st_mtime < now - 12 * 3600:
            shutil.rmtree(path)
    tmpdir = tempfile.mkdtemp(prefix="app_", dir=APP_TMP_WORKDIRS)

    # Copy existing app scripts, conf, ... if an app arg was provided
    if app:
        os.system(f"cp -a {APPS_SETTING_PATH}/{app}/* {tmpdir}")

    return tmpdir


def is_true(arg):
    """
    Convert a string into a boolean

    Keyword arguments:
        arg -- The string to convert

    Returns:
        Boolean

    """
    if isinstance(arg, bool):
        return arg
    elif isinstance(arg, str):
        return arg.lower() in ["yes", "true", "on"]
    else:
        logger.debug("arg should be a boolean or a string, got %r", arg)
        return True if arg else False


def unstable_apps():

    output = []

    for infos in app_list(full=True)["apps"]:

        if not infos.get("from_catalog") or infos.get("from_catalog").get("state") in [
            "inprogress",
            "notworking",
        ]:
            output.append(infos["id"])

    return output


def _assert_system_is_sane_for_app(manifest, when):

    from yunohost.service import service_status

    logger.debug("Checking that required services are up and running...")

    services = manifest.get("services", [])

    # Some apps use php-fpm or php5-fpm which is now php7.0-fpm
    def replace_alias(service):
        if service in ["php-fpm", "php5-fpm", "php7.0-fpm"]:
            return "php7.3-fpm"
        else:
            return service

    services = [replace_alias(s) for s in services]

    # We only check those, mostly to ignore "custom" services
    # (added by apps) and because those are the most popular
    # services
    service_filter = ["nginx", "php7.3-fpm", "mysql", "postfix"]
    services = [str(s) for s in services if s in service_filter]

    if "nginx" not in services:
        services = ["nginx"] + services
    if "fail2ban" not in services:
        services.append("fail2ban")

    # Wait if a service is reloading
    test_nb = 0
    while test_nb < 16:
        if not any(s for s in services if service_status(s)["status"] == "reloading"):
            break
        time.sleep(0.5)
        test_nb += 1

    # List services currently down and raise an exception if any are found
    services_status = {s: service_status(s) for s in services}
    faulty_services = [
        f"{s} ({status['status']})"
        for s, status in services_status.items()
        if status["status"] != "running"
    ]

    if faulty_services:
        if when == "pre":
            raise YunohostValidationError(
                "app_action_cannot_be_ran_because_required_services_down",
                services=", ".join(faulty_services),
            )
        elif when == "post":
            raise YunohostError(
                "app_action_broke_system", services=", ".join(faulty_services)
            )

    if packages.dpkg_is_broken():
        if when == "pre":
            raise YunohostValidationError("dpkg_is_broken")
        elif when == "post":
            raise YunohostError("this_action_broke_dpkg")

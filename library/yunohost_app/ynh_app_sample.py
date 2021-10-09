#!/usr/bin/python3

# Copyright: (c) 2021, Uptime <terry.jones@example.org>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

# https://github.com/YunoHost/yunohost/pull/951#issue-604908363
from ansible.module_utils.basic import AnsibleModule
from yunohost.app import app_info, app_install, app_list
import yunohost
import sys
sys.path.insert(0, "/usr/lib/moulinette/")
# possibly set debug=True if needed, but when writing script supposedly you ain't debugging yunohost itself, and full log is still available in yunohost-cli.log and yunohost log list...
yunohost.init_logging()
yunohost.init_i18n()

# user_create(username="foo", ...)


ANSIBLE_METADATA = {
    'metadata_version': '1.1',
    'status': ['preview'],
    'supported_by': 'community'
}

DOCUMENTATION = '''
---
module: ynh_app

short_description: Module to manage users in Yunohost based on Yunohost Python library

version_added: "2.4"

description:
    - "Let's read the actionsmap file of yunohost to do cool bindings, possibly idempotent and stateful like Ansible intended to."

# options:
#     name:
#         description:
#             - This is the message to send to the test module
#         required: true
#     new:
#         description:
#             - Control to demo if the result of this module is changed or not
#         required: false

extends_documentation_fragment:
    - azure

author:
    - Your Name (@yourhandle)
'''

# EXAMPLES = '''
# # Pass in a message
# - name: Test with a message
#   my_test:
#     name: hello world

# # pass in a message and have changed true
# - name: Test with a message and changed output
#   my_test:
#     name: hello world
#     new: true

# # fail the module
# - name: Test failure of the module
#   my_test:
#     name: fail me
# '''

# RETURN = '''
# original_message:
#     description: The original name param that was passed in
#     type: str
#     returned: always
# message:
#     description: The output message that the test module generates
#     type: str
#     returned: always
# '''


def run_module():
    # define available arguments/parameters a user can pass to the module
    module_args = dict(
        name=dict(type='str', required=True),
        label=dict(type='str', required=False, default=name),
        args=dict(type='list', elements='str', required=False)
    )

    # seed the result dict in the object
    # we primarily care about changed and state
    # change is if this module effectively modified the target
    # state will include any data that you want your module to pass back
    # for consumption, for example, in a subsequent task
    result = dict(
        changed=False,
        original_message='',
        message=''
    )

    # the AnsibleModule object will be our abstraction working with Ansible
    # this includes instantiation, a couple of common attr would be the
    # args/params passed to the execution, as well as if the module
    # supports check mode
    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True
    )

    # if the user is working with this module in only check mode we do not
    # want to make any changes to the environment, just return the current
    # state with no modifications
    if module.check_mode:
        module.exit_json(**result)

    # manipulate or modify the state as needed (this is going to be the
    # part where your module will do what it needs to do)
    result['original_message'] = module.params['name']
    result['message'] = 'goodbye'

    # use whatever logic you need to determine whether or not this module
    # made any modifications to your target
    if module.params['new']:
        result['changed'] = True

    # during the execution of the module, if there is an exception or a
    # conditional state that effectively causes a failure, run
    # AnsibleModule.fail_json() to pass in the message and the result
    if module.params['name'] == 'fail me':
        module.fail_json(msg='You requested this to fail', **result)

    # in the event of a successful module execution, you will want to
    # simple AnsibleModule.exit_json(), passing the key/value results
    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()


# #############################
# #            App            #
# #############################
# app:
#     category_help: Manage apps
#     actions:

#         ### app_list()
#         list:
#             action_help: List apps
#             api: GET /apps
#             arguments:
#                 -f:
#                     full: --filter
#                     help: Name filter of app_id or app_name
#                 -r:
#                     full: --raw
#                     help: Return the full app_dict
#                     action: store_true
#                 -i:
#                     full: --installed
#                     help: Return only installed apps
#                     action: store_true
#                 -b:
#                     full: --with-backup
#                     help: Return only apps with backup feature (force --installed filter)
#                     action: store_true

#         ### app_info()
#         info:
#             action_help: Get information about an installed app
#             api: GET /apps/<app>
#             arguments:
#                 app:
#                     help: Specific app ID
#                 -s:
#                     full: --show-status
#                     help: Show app installation status
#                     action: store_true
#                 -r:
#                     full: --raw
#                     help: Return the full app_dict
#                     action: store_true

#         ### app_map()
#         map:
#             action_help: List apps by domain
#             api: GET /appsmap
#             arguments:
#                 -a:
#                     full: --app
#                     help: Specific app to map
#                 -r:
#                     full: --raw
#                     help: Return complete dict
#                     action: store_true
#                 -u:
#                     full: --user
#                     help: Allowed app map for a user
#                     extra:
#                         pattern: *pattern_username

#         ### app_install()
#         install:
#             action_help: Install apps
#             api: POST /apps
#             arguments:
#                 app:
#                     help: Name, local path or git URL of the app to install
#                 -l:
#                     full: --label
#                     help: Custom name for the app
#                 -a:
#                     full: --args
#                     help: Serialized arguments for app script (i.e. "domain=domain.tld&path=/path")
#                 -n:
#                     full: --no-remove-on-failure
#                     help: Debug option to avoid removing the app on a failed installation
#                     action: store_true
#                 -f:
#                     full: --force
#                     help: Do not ask confirmation if the app is not safe to use (low quality, experimental or 3rd party)
#                     action: store_true

#         ### app_remove() TODO: Write help
#         remove:
#             action_help: Remove app
#             api: DELETE /apps/<app>
#             arguments:
#                 app:
#                     help: App(s) to delete

#         ### app_upgrade()
#         upgrade:
#             action_help: Upgrade app
#             api: PUT /upgrade/apps
#             arguments:
#                 app:
#                     help: App(s) to upgrade (default all)
#                     nargs: "*"
#                 -u:
#                     full: --url
#                     help: Git url to fetch for upgrade
#                 -f:
#                     full: --file
#                     help: Folder or tarball for upgrade

#         ### app_change_url()
#         change-url:
#             action_help: Change app's URL
#             api: PUT /apps/<app>/changeurl
#             arguments:
#                 app:
#                     help: Target app instance name
#                 -d:
#                     full: --domain
#                     help: New app domain on which the application will be moved
#                     extra:
#                         ask: ask_new_domain
#                         pattern: *pattern_domain
#                         required: True
#                 -p:
#                     full: --path
#                     help: New path at which the application will be moved
#                     extra:
#                         ask: ask_new_path
#                         required: True

#         ### app_setting()
#         setting:
#             action_help: Set or get an app setting value
#             api: GET /apps/<app>/settings
#             arguments:
#                 app:
#                     help: App ID
#                 key:
#                     help: Key to get/set
#                 -v:
#                     full: --value
#                     help: Value to set
#                 -d:
#                     full: --delete
#                     help: Delete the key
#                     action: store_true

#         ### app_checkport()
#         checkport:
#             action_help: Check availability of a local port
#             api: GET /tools/checkport
#             deprecated: true
#             arguments:
#                 port:
#                     help: Port to check
#                     extra:
#                         pattern: &pattern_port
#                             - !!str ^([0-9]{1,4}|[1-5][0-9]{4}|6[0-4][0-9]{3}|65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5])$
#                             - "pattern_port"

#         ### app_checkurl()
#         checkurl:
#             action_help: Check availability of a web path
#             api: GET /tools/checkurl
#             deprecated: True
#             arguments:
#                 url:
#                     help: Url to check
#                 -a:
#                     full: --app
#                     help: Write domain & path to app settings for further checks

#         ### app_register_url()
#         register-url:
#             action_help: Book/register a web path for a given app
#             api: PUT /tools/registerurl
#             arguments:
#                 app:
#                     help: App which will use the web path
#                 domain:
#                     help: The domain on which the app should be registered (e.g. your.domain.tld)
#                 path:
#                     help: The path to be registered (e.g. /coffee)


#         ### app_initdb()
#         initdb:
#             action_help: Create database and initialize it with optionnal attached script
#             api: POST /tools/initdb
#             deprecated: true
#             arguments:
#                 user:
#                     help: Name of the DB user
#                 -p:
#                     full: --password
#                     help: Password of the DB (generated unless set)
#                 -d:
#                     full: --db
#                     help: DB name (user unless set)
#                 -s:
#                     full: --sql
#                     help: Initial SQL file

#         ### app_debug()
#         debug:
#             action_help: Display all debug informations for an application
#             api: GET /apps/<app>/debug
#             arguments:
#                 app:
#                     help: App name

#         ### app_makedefault()
#         makedefault:
#             action_help: Redirect domain root to an app
#             api: PUT /apps/<app>/default
#             arguments:
#                 app:
#                     help: App name to put on domain root
#                 -d:
#                     full: --domain
#                     help: Specific domain to put app on (the app domain by default)

#         ### app_ssowatconf()
#         ssowatconf:
#             action_help: Regenerate SSOwat configuration file
#             api: PUT /ssowatconf

#         ### app_change_label()
#         change-label:
#             action_help: Change app label
#             api: PUT /apps/<app>/label
#             arguments:
#                 app:
#                     help: App ID
#                 new_label:
#                     help: New app label

#         ### app_addaccess() TODO: Write help
#         addaccess:
#             action_help: Grant access right to users (everyone by default)
#             api: PUT /access
#             arguments:
#                 apps:
#                     nargs: "+"
#                 -u:
#                     full: --users
#                     nargs: "*"

#         ### app_removeaccess() TODO: Write help
#         removeaccess:
#             action_help: Revoke access right to users (everyone by default)
#             api: DELETE /access
#             arguments:
#                 apps:
#                     nargs: "+"
#                 -u:
#                     full: --users
#                     nargs: "*"

#         ### app_clearaccess()
#         clearaccess:
#             action_help: Reset access rights for the app
#             api: POST /access
#             arguments:
#                 apps:
#                     nargs: "+"

#     subcategories:

#       action:
#           subcategory_help: Handle apps actions
#           actions:

#               ### app_action_list()
#               list:
#                   action_help: List app actions
#                   api: GET /apps/<app>/actions
#                   arguments:
#                     app:
#                         help: App name

#               ### app_action_run()
#               run:
#                   action_help: Run app action
#                   api: PUT /apps/<app>/actions/<action>
#                   arguments:
#                     app:
#                         help: App name
#                     action:
#                         help: action id
#                     -a:
#                         full: --args
#                         help: Serialized arguments for app script (i.e. "domain=domain.tld&path=/path")

#       config:
#           subcategory_help: Applications configuration panel
#           actions:

#               ### app_config_show_panel()
#               show-panel:
#                   action_help: show config panel for the application
#                   api: GET /apps/<app>/config-panel
#                   arguments:
#                       app:
#                           help: App name

#               ### app_config_apply()
#               apply:
#                   action_help: apply the new configuration
#                   api: POST /apps/<app>/config
#                   arguments:
#                       app:
#                           help: App name
#                       -a:
#                           full: --args
#                           help: Serialized arguments for new configuration (i.e. "domain=domain.tld&path=/path")

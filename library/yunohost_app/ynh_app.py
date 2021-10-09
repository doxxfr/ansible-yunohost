#!/usr/bin/python

# Copyright: (c) 2018, Terry Jones <terry.jones@example.org>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from subprocess import Popen, PIPE
import re
from ansible.module_utils.basic import AnsibleModule
ANSIBLE_METADATA = {
    'metadata_version': '0.1',
    'status': ['preview'],
    'supported_by': 'community'
}

DOCUMENTATION = '''
---
module: ynh_app

short_description: Modify Yunohost app

description:
    - "This module calls yunohost CLI to modify apps install"
                
options:
    name:
        description:
            -  Name, local path or git URL of the app to install
        required: true
    label:
        description:
            -  Custom name for the app
        required: false
    args:
        description:
            - Serialized arguments for app script (i.e. "domain=domain.tld&path=/path")
        required: false
    force:
        description: 
            - Do not ask confirmation if the app is not safe to use (low quality, experimental or 3rd party)
        required: false

author:
    - You
'''

EXAMPLES = '''
# Change app args
- name: Update the default domain
  ynh_app:
    name: wordpress
    args: domain=google.com&path=/
    label: Wordpress
    # force: yes
'''

RETURN = '''
old_value:
    description: The previous value of the configuration key
    type: str
new_value:
    description: The new value of the configuration key
    type: str
    returned: always
'''


def run_module():
    # define available arguments/parameters a user can pass to the module
    module_args = dict(
        name=dict(type='str', required=True),
        label=dict(type='str', required=False, default=module.params['name']),
        args=dict(type='list', elements='str', required=False),
        force=dict(type='bool', required=False, default=True),
    )

    # seed the result dict in the object
    # we primarily care about changed and state
    # change is if this module effectively modified the target
    # state will include any data that you want your module to pass back
    # for consumption, for example, in a subsequent task
    result = dict(
        changed=False,
        old_value=None,
    )

    # the AnsibleModule object will be our abstraction working with Ansible
    # this includes instantiation, a couple of common attr would be the
    # args/params passed to the execution, as well as if the module
    # supports check mode
    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True
    )

    app_name = module.params['name']
    app_label = module.params['label']
    app_args = module.params['args']
    force = module.params['force']

    # new_value = module.params['value']
    # sep = module.params['sep']
    result['name'] = app_name
    result['app_label'] = app_label

    # Try to execute YNH CLI
    try:
        previous = Popen(
            ['/usr/bin/yunohost',
             'app', 'info', app_name],
            stdout=PIPE,
            stderr=PIPE
        )

        stdout, stderr = previous.communicate()
        stdout = stdout.decode('UTF-8')
    except OSError as e:
        module.fail_json(msg='Could not run CLI: ' + str(e), **result)

    old_value = None
    if stdout.startswith(key_name+" = "):
        # Grab the value, omitting newline
        old_value = stdout[len(key_name) + 3:-1]

    result['old_value'] = old_value

    if old_value != new_value:
        result['changed'] = True

    # If no change or check mode, quit now
    if module.check_mode or result['changed'] == False:
        module.exit_json(**result)

        change = Popen(
            ['/usr/bin/yunohost',
                'app', 'install', app_name, '--label', app_label, '--args', app_args, '--force'],
            # domain=domain.tld&path=/path
            stdout=PIPE,
            stderr=PIPE
        )
    # # Else, modify config
    # if sep in key_name:
    #     (key_base, _, subkey) = key_name.rpartition(sep)
    #     # use addKey
    #     change = Popen(
    #         ['/usr/libexec/lemonldap-ng/bin/lemonldap-ng-cli',
    #             '-yes', '1', '-safe', '1', '-sep', sep, 'addKey', key_base, subkey, new_value],
    #         stdout=PIPE,
    #         stderr=PIPE
    #     )
    # else:
    #     # regular set
    #     change = Popen(
    #         ['/usr/libexec/lemonldap-ng/bin/lemonldap-ng-cli',
    #             '-yes', '1', '-safe', '1', 'set', key_name, new_value],
    #         stdout=PIPE,
    #         stderr=PIPE
    #     )
    stdout, stderr = change.communicate()
    stdout = stdout.decode('UTF-8')

    if change.returncode == 0:
        result['stdout'] = stdout
        module.exit_json(**result)
    else:
        module.fail_json(msg='CLI rejected configuration change',
                         rc=change.returncode, stdout=stdout, stderr=stderr, **result)


def main():
    run_module()


if __name__ == '__main__':
    main()

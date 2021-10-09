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
module: lemonldap_config

short_description: Modify LemonLDAP::NG configuration

description:
    - "This module calls lemonldap-ng-cli to modify the LemonLDAP::NG configuration"

options:
    name:
        description:
            - Configuration key to modify
        required: true
    value:
        description:
            - Value of the configuration to enforce
        required: true
    sep:
        description:
            - Separator value
        required: false

author:
    - Maxime Besson
    - Worteks
'''

EXAMPLES = '''
# Change domain
- name: Update the default domain
  lemonldap_config:
    name: domain
    value: mydomain.net
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
        value=dict(type='str', required=True),
        sep=dict(type='str', required=False, default='/'),
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

    key_name = module.params['name']
    new_value = module.params['value']
    sep = module.params['sep']
    result['name'] = key_name
    result['new_value'] = new_value

    # Try to execute LLNG-cli
    try:
        previous = Popen(
            ['/usr/libexec/lemonldap-ng/bin/lemonldap-ng-cli',
                'get', key_name],
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

    # Else, modify config
    if sep in key_name:
        (key_base, _, subkey) = key_name.rpartition(sep)
        # use addKey
        change = Popen(
            ['/usr/libexec/lemonldap-ng/bin/lemonldap-ng-cli',
                '-yes', '1', '-safe', '1', '-sep', sep, 'addKey', key_base, subkey, new_value],
            stdout=PIPE,
            stderr=PIPE
        )
    else:
        # regular set
        change = Popen(
            ['/usr/libexec/lemonldap-ng/bin/lemonldap-ng-cli',
                '-yes', '1', '-safe', '1', 'set', key_name, new_value],
            stdout=PIPE,
            stderr=PIPE
        )
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

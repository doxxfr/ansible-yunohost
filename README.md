ansible-yunohost
=========

Deploy Yunohost with Ansible !

<!-- [![Build Status](https://travis-ci.org/)](https://travis-ci.org/) -->

Requirements
------------

- Vanilla Debian Stretch server with passwordless sudo or root access

Role Variables
--------------

See [defaults/main.yml](defaults/main.yml)

Example Playbook
----------------
```yml
- name: Install Yunohost on Debian Servers
  hosts: yunohost_servers


  pre_tasks:
    - name: Update all packages and index
      apt:
        upgrade: dist
        update_cache: yes

  roles:
    - role: e_lie.ansible_yunohost
      vars:
        ynh_install_script:

        ynh_main_domain: example.com

        ynh_admin_password: mysecurepasswd 

        ynh_users:
          - name: user1
            pass: p@ssw0rd
            firstname: Jane
            lastname: Doe

            # yo have to define an address based on one of the yunohost domains 
            mail: jane.doe@example.com 

        ynh_apps:
          - label: Tiny Tiny RSS
            link: ttrss # It can be the name of an official app or a github link
            args: # Provide here args. Path and domain are mandatory, other args depend of the app (cf manifest.json of app).
              path: /ttrss
              domain: example.com

```

License
-------

GPL-3.0

ansible-yunohost
=========

Deploy Yunohost with Ansible !

Requirements
------------

None.

Role Variables
--------------

```yml
# Keep script defaults for a production server
ynh_install_script:
  url: https://install.yunohost.org
  checksum: sha256:982bda27a012bfbf37235b10805e99817be6aedd27420fe33218bd54d502a57a

ynh_install_domain_certs: True

ynh_main_domain: example.com
ynh_extra_domains:
    - example2.com
    - example3.com

ynh_admin_password: MYINSECUREPWD_PLZ_OVERRIDE_THIS

# When using yunohost autoconfigured domain names (e.g. noho.st), ignore the dyndns config
ynh_ignore_dyndns_server: False

# The list of users.
ynh_users:
  - name: user1
    pass: p@ssw0rd
    firstname: Jane
    lastname: Doe
    mail: jane.doe@example.com # yo have to define an address based on one of the yunohost domains 

# The list of apps you want on the server
ynh_apps:
  - label: Tiny Tiny RSS # Label is important, it's a reference for the Playbook.
    link: ttrss # It can be the name of an official app or a github link
    args: # Provide here args. Path and domain are mandatory, other args depend of the app (cf manifest.json of app).
      path: /ttrss
      domain: example.com


```

Example usage:
```yml
yunohost:
  # Link to the install script
  install_script_url: https://raw.githubusercontent.com/YunoHost/install_script/master/install_yunohost
  # The main domain, then a list of other domains.
  domain: example.com
  extra_domains:
    - example2.com
    - example3.com
  # Yunohost admin password
  admin_password: MYINSECUREPWD_PLZ_OVERRIDE_THIS
  # If you don't want to use a noho.st url
  ignore_dyndns: False
  # The list of apps you want to install.
  apps:
    - label: Tiny Tiny RSS # Label is important, it's a reference for the Playbook.
      link: ttrss # It can be the name of an official app or a github link
      args: # Provide here args. Path and domain are mandatory, other args depend of the app (cf manifest.json of app).
        path: /ttrss
        domain: example.com
  # The list of users.
  users:
    - name: user1
      pass: p@ssw0rd
      firstname: Jane
      lastname: Doe
      mail: jane.doe@example.com
```

Dependencies
------------

None.

Example Playbook
----------------
```yml
- name: Provision servers
  hosts: all
  remote_user: root
  pre_tasks:
    - name: Update all packages and index
      apt:
        upgrade: dist
        update_cache: yes

  roles:
     - { role: e_lie.ansible_yunohost }
```

License
-------

GPL-3.0

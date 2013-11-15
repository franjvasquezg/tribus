#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2013 Desarrolladores de Tribus
#
# This file is part of Tribus.
#
# Tribus is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Tribus is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


import os
import pwd
import sys
import site
from fabric.api import *

from tribus import BASEDIR
from tribus.config.ldap import (AUTH_LDAP_SERVER_URI, AUTH_LDAP_BASE, AUTH_LDAP_BIND_DN,
                               AUTH_LDAP_BIND_PASSWORD)
from tribus.config.pkg import (debian_run_dependencies, debian_build_dependencies,
                              f_workenv_preseed, f_sql_preseed, f_users_ldif,
                              f_python_dependencies)


def development():
    env.user = pwd.getpwuid(os.getuid()).pw_name
    env.root = 'root'
    env.environment = 'development'
    env.hosts = ['localhost']
    env.basedir = BASEDIR
    env.virtualenv_dir = os.path.join(env.basedir, 'virtualenv')
    env.virtualenv_args = ' '.join(['--clear', '--no-site-packages', '--distribute'])
    env.virtualenv_activate = os.path.join(env.virtualenv_dir, 'bin', 'activate')
    env.settings = 'tribus.config.web'
    env.sudo_prompt = 'Executed'
    env.f_python_dependencies = f_python_dependencies


def environment():
    configure_sudo()
    preseed_packages()
    install_packages(debian_build_dependencies)
    install_packages(debian_run_dependencies)
    drop_mongo()
    configure_postgres()
    populate_ldap()
    create_virtualenv()
    update_virtualenv()
    configure_django()
    deconfigure_sudo()
    
    
def resetdb():
    configure_sudo()
    drop_mongo()
    configure_postgres()
    configure_django()
    deconfigure_sudo()
    
    
def filldb():
    py_activate_virtualenv()
    from tribus.common.recorder import init_package_cache
    init_package_cache()


def configure_sudo():
    with settings(command='su root -c "echo \'%(user)s ALL= NOPASSWD: ALL\' > /etc/sudoers.d/tribus"' % env):
        local('%(command)s' % env)


def deconfigure_sudo():
    with settings(command='sudo /bin/bash -c "rm -rf /etc/sudoers.d/tribus"' % env):
        local('%(command)s' % env)


def preseed_packages():
    with settings(command='sudo /bin/bash -c "debconf-set-selections %s"' % f_workenv_preseed):
        local('%(command)s' % env)


def install_packages(dependencies):
    with settings(command='sudo /bin/bash -c "DEBIAN_FRONTEND=noninteractive \
aptitude install --assume-yes --allow-untrusted \
-o DPkg::Options::=--force-confmiss \
-o DPkg::Options::=--force-confnew \
-o DPkg::Options::=--force-overwrite \
%s"' % ' '.join(dependencies)):
        local('%(command)s' % env)


def configure_postgres():
    with settings(command='sudo /bin/bash -c "echo \'postgres:tribus\' | chpasswd"'):
        local('%(command)s' % env)

    with settings(command='cp %s /tmp/' % f_sql_preseed):
        local('%(command)s' % env)

    with settings(command='sudo /bin/bash -c "sudo -i -u postgres /bin/sh -c \'psql -f /tmp/preseed-db.sql\'"'):
        local('%(command)s' % env)


def populate_ldap():
    env.ldap_passwd = AUTH_LDAP_BIND_PASSWORD
    env.ldap_writer = AUTH_LDAP_BIND_DN
    env.ldap_server = AUTH_LDAP_SERVER_URI
    env.ldap_base = AUTH_LDAP_BASE
    env.users_ldif = f_users_ldif
    with settings(command='ldapsearch -x -w "%(ldap_passwd)s" \
-D "%(ldap_writer)s" -H "%(ldap_server)s" -b "%(ldap_base)s" \
-LLL "(uid=*)" | perl -p00e \'s/\\r?\\n //g\' | grep "dn: "| \
sed \'s/dn: //g\' | sed \'s/ /_@_/g\'' % env):
        ldap_entries = local('%(command)s' % env, capture=True)

    for ldap_entry in ldap_entries.split():
        env.ldap_entry = ldap_entry
        with settings(command='ldapdelete -x -w "%(ldap_passwd)s" \
-D "%(ldap_writer)s" -H "%(ldap_server)s" "%(ldap_entry)s"' % env):
            local('%(command)s' % env)

    with settings(command='ldapadd -x -w "%(ldap_passwd)s" \
-D "%(ldap_writer)s" -H "%(ldap_server)s" -f "%(users_ldif)s"' % env):
        local('%(command)s' % env)


def create_virtualenv():
    with cd('%(basedir)s' % env):
        with settings(command='virtualenv %(virtualenv_args)s %(virtualenv_dir)s' % env):
            local('%(command)s' % env)


def update_virtualenv():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s pip install -r %(f_python_dependencies)s' % env)


def py_activate_virtualenv():
    os.environ['PATH'] = os.path.join(env.virtualenv_dir, 'bin') + os.pathsep + os.environ['PATH']
    site.addsitedir(os.path.join(env.virtualenv_dir, 'lib', 'python%s' % sys.version[:3], 'site-packages'))
    sys.prefix = env.virtualenv_dir
    sys.path.insert(0, env.virtualenv_dir)


def configure_django():
    syncdb_django()
    createsuperuser_django()

def drop_mongo():
    with settings(command='mongo tribus --eval \'db.dropDatabase()\'' % env):
        local('%(command)s' % env)


def createsuperuser_django():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python manage.py createsuperuser --noinput --username admin --email admin@localhost.com' % env)

    py_activate_virtualenv()

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tribus.config.web")
    from django.contrib.auth.models import User

    u = User.objects.get(username__exact='admin')
    u.set_password('tribus')
    u.save()


def syncdb_django():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python manage.py syncdb --noinput' % env)
            local('%(command)s python manage.py migrate' % env)


def runserver_django():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python manage.py runserver' % env)

def shell_django():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python manage.py shell' % env)

def update_catalog():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py update_catalog' % env)


def extract_messages():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py extract_messages' % env)


def compile_catalog():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py compile_catalog' % env)


def init_catalog():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py init_catalog' % env)


def build_sphinx():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py build_sphinx' % env)


def build_css():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py build_css' % env)


def build_js():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py build_js' % env)


def build_man():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py build_man' % env)


def build():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py build' % env)


def clean_css():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py clean_css' % env)


def clean_js():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py clean_js' % env)


def clean_sphinx():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py clean_sphinx' % env)


def clean_mo():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py clean_mo' % env)


def clean_man():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py clean_man' % env)


def clean_dist():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py clean_dist' % env)


def clean_pyc():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py clean_pyc' % env)


def clean():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py clean' % env)


def sdist():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py sdist' % env)


def bdist():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py bdist' % env)


def install():
    with cd('%(basedir)s' % env):
        with settings(command='. %(virtualenv_activate)s;' % env):
            local('%(command)s python setup.py install' % env)
#!/usr/bin/env python3

import os
import sys

from lib import (
    awx_get_inventory_file,
    awx_get_vars,
    check_bastion_host,
    fill_bastion_vars,
    find_executable,
    get_hostvars,
    has_remote_command,
    manage_conf_file,
    parse_bastion_env_vars,
    parse_ssh_argv,
)


def main():
    argv = list(sys.argv[1:])  # Copy

    remote_user = None
    remote_port = 22
    default_configuration_file = "/etc/ovh/bastion/config.yml"

    ssh = find_executable("ssh")
    if not ssh:
        sys.exit("bastion wrapper: no ssh executable found in PATH")

    # nothing to proxy, hand the arguments over to ssh untouched
    if not has_remote_command(argv):
        os.execv(ssh, ["ssh"] + argv)

    options, _, remaining = parse_ssh_argv(argv)
    host = remaining[0]
    cmd = " ".join(remaining[1:])

    # check if bastion_vars are passed as env vars in the playbook
    # may be usefull if the ansible controller manage many bastions
    # example :
    # - hosts: all
    #   gather_facts: false
    #   environment:
    #     BASTION_USER: "{{ bastion_user }}"
    #     BASTION_HOST: "{{ bastion_host }}"
    #     BASTION_PORT: "{{ bastion_port }}"
    bastion_host, bastion_port, bastion_user = parse_bastion_env_vars(cmd)

    # in some cases (AWX in a non containerised environment for instance), the environment is overridden by the job
    # so we are not able to get the BASTION vars
    # if some vars are still undefined, try to load them from a configuration file
    if not bastion_host or not bastion_port or not bastion_user:
        bastion_host, bastion_port, bastion_user = manage_conf_file(
            os.environ.get("BASTION_CONF_FILE", default_configuration_file),
            bastion_host,
            bastion_port,
            bastion_user,
        )

    # lookup on the inventory may take some time, depending on the source, so use it only if not defined elsewhere
    # it seems like some module like template does not send env vars too...
    if not bastion_host or not bastion_port or not bastion_user:
        # check if running on AWX, we'll get the vars in a different way
        awx_inventory_file = awx_get_inventory_file()
        if os.path.exists(awx_inventory_file):
            hostvar = awx_get_vars(host, awx_inventory_file)
        else:
            hostvar = get_hostvars(host)  # dict

        bastion_host, bastion_port, bastion_user = fill_bastion_vars(
            hostvar, bastion_host, bastion_port, bastion_user
        )

    check_bastion_host(bastion_host)

    for i, e in enumerate(options):

        if e.startswith("User="):
            remote_user = e.split("=")[-1]
            options[i] = "User={}".format(bastion_user)
        elif e.startswith("Port="):
            remote_port = e.split("=")[-1]
            options[i] = "Port={}".format(bastion_port)

    # syscall exec
    args = (
        [
            "ssh",
            "-p",
            bastion_port,
            "-q",
            "-o",
            "StrictHostKeyChecking=no",
            "-l",
            bastion_user,
            bastion_host,
            "-T",
        ]
        + options
        + [
            "--",
            "-q",
            "-T",
            "--never-escape",
            "--user",
            remote_user,
            "--port",
            remote_port,
            host,
            "--",
            cmd,
        ]
    )
    os.execv(
        ssh,
        [str(e).strip() for e in args],  # execv() arg 2 must contain only strings
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3

import os
import sys

from lib import (
    check_bastion_host,
    fill_bastion_vars,
    find_executable,
    get_hostvars,
    manage_conf_file,
    parse_bastion_env_vars,
)


def main():
    argv = list(sys.argv[1:])  # Copy

    remote_user = None
    remote_port = 22
    default_configuration_file = "/etc/ovh/bastion/config.yml"

    ssh = find_executable("ssh")
    if not ssh:
        sys.exit("bastion wrapper: no ssh executable found in PATH")

    iteration = enumerate(argv)
    sshcmdline = []
    for i, e in iteration:
        # a trailing option has no value, and reading one raises
        value = argv[i + 1] if i + 1 < len(argv) else ""
        if e == "-l" and value:
            remote_user = value
            next(iteration)
        elif e == "-p" and value:
            remote_port = value
            next(iteration)
        elif e == "-o" and value.startswith("User="):
            remote_user = value.split("=")[-1]
            next(iteration)
        elif e == "-o" and value.startswith("Port="):
            remote_port = value.split("=")[-1]
            next(iteration)
        elif e == "--":
            sshcmdline.extend(argv[i + 1 :])
            break
        else:
            sshcmdline.append(e)

    if len(sshcmdline) < 2:
        sys.exit("bastion wrapper: no host and scp command to proxy")

    scpcmd = sshcmdline.pop()
    host = sshcmdline.pop()

    # check if bastion_vars are passed as env vars in the playbook
    # may be usefull if the ansible controller manage many bastions
    bastion_host, bastion_port, bastion_user = parse_bastion_env_vars(scpcmd)

    # the bastion reads the command as a single argument
    scpcmd = scpcmd.replace("#", "##").replace(" ", "#")

    # read from configuration file
    if not bastion_host or not bastion_port or not bastion_user:
        bastion_host, bastion_port, bastion_user = manage_conf_file(
            os.getenv("BASTION_CONF_FILE", default_configuration_file),
            bastion_host,
            bastion_port,
            bastion_user,
        )

    # lookup on the inventory may take some time, depending on the source, so use it only if not defined elsewhere
    # it seems like some module like template does not send env vars too...
    if not bastion_host or not bastion_port or not bastion_user:
        hostvar = get_hostvars(host)  # dict

        bastion_host, bastion_port, bastion_user = fill_bastion_vars(
            hostvar, bastion_host, bastion_port, bastion_user
        )

    check_bastion_host(bastion_host)

    # syscall exec
    args = (
        [
            "ssh",
            "{}@{}".format(bastion_user, bastion_host),
            "-p",
            bastion_port,
            "-o",
            "StrictHostKeyChecking=no",
            "-T",
        ]
        + sshcmdline
        + [
            "--",
            "--user",
            remote_user,
            "--port",
            remote_port,
            "--host",
            host,
            "--osh",
            "scp",
            "--scp-cmd",
            scpcmd,
        ]
    )

    os.execv(
        ssh,
        [str(e).strip() for e in args],  # execv() arg 2 must contain only strings
    )


if __name__ == "__main__":
    main()

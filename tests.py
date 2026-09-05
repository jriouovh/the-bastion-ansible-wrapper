import os
import sys

import pytest
from yaml import dump

import lib
import scpwrapper
import sftpwrapper
import sshwrapper
from lib import (
    awx_get_inventory_file,
    awx_get_vars,
    check_bastion_host,
    find_executable,
    fill_bastion_vars,
    get_bastion_vars,
    get_var_within,
    has_remote_command,
    manage_conf_file,
    parse_bastion_env_vars,
    parse_ssh_argv,
    source_enabled,
)

BASTION_HOST = "my_bastion"
BASTION_PORT = 22
BASTION_USER = "my_bastion_user"
BASTION_CONF_FILE = "/tmp/test_bastion_conf_file.yml"
BASTION_HOST_ONLY_CONF_FILE = "/tmp/test_bastion_host_only_conf_file.yml"

real_execv = os.execv

AWX_COMMAND = (
    "/bin/sh -c 'BASTION_USER={} BASTION_HOST={} BASTION_PORT={} "
    "/usr/bin/python3 && sleep 0'".format(BASTION_USER, BASTION_HOST, BASTION_PORT)
)

BASTION_PATH_COMMAND = (
    "/bin/sh -c 'chmod u+x /runner/bastion_host_role/x.py && " + AWX_COMMAND
)

SCP_COMMAND = "scp -t /etc/ansible/bastion_hosts.yml"

SCP_ARGS = [
    "-x",
    "-oPermitLocalCommand=no",
    "-l",
    "deploy",
    "--",
    "127.0.0.1",
    SCP_COMMAND,
]

SFTP_ARGS = [
    "-oForwardX11 no",
    "-oControlMaster no",
    "-o",
    "IdentityFile=/tmp/id_ansible",
    "-o",
    "Port=2222",
    "-o",
    "User=deploy",
    "-s",
    "--",
    "127.0.0.1",
    "sftp",
]

SSH_ARGS = [
    "-C",
    "-o",
    "ControlMaster=auto",
    "-o",
    "User=deploy",
    "-o",
    "Port=2222",
    "127.0.0.1",
]


class Executed(Exception):
    """Hold the arguments the wrapper handed over to execv"""


def fake_execv(path, args):
    raise Executed(args)


def exec_wrapper(wrapper, argv, conf_file="/nonexistent.yml", path=None):
    """Run a wrapper and return the arguments it hands over to execv"""
    sys.argv = ["wrapper.py"] + argv
    os.environ["BASTION_CONF_FILE"] = conf_file
    os.environ["AWX_RUN_DIR"] = "/nonexistent"
    real_path = os.environ["PATH"]
    if path is not None:
        os.environ["PATH"] = path
    os.execv = fake_execv
    try:
        wrapper.main()
    except Executed as executed:
        return executed.args[0]
    finally:
        os.execv = real_execv
        os.environ["PATH"] = real_path
        os.environ.pop("BASTION_CONF_FILE")
        os.environ.pop("AWX_RUN_DIR")

    raise AssertionError("the wrapper did not exec ssh")


def test_manage_conf_file_bastion_host_undefined():
    bastion_host, bastion_port, bastion_user = manage_conf_file(
        BASTION_CONF_FILE, None, BASTION_PORT, BASTION_USER
    )
    assert bastion_host == BASTION_HOST


def test_manage_conf_file_bastion_port_undefined():
    bastion_host, bastion_port, bastion_user = manage_conf_file(
        BASTION_CONF_FILE, BASTION_HOST, None, BASTION_USER
    )
    assert bastion_port == BASTION_PORT


def test_manage_conf_file_bastion_user_undefined():
    bastion_host, bastion_port, bastion_user = manage_conf_file(
        BASTION_CONF_FILE, BASTION_HOST, BASTION_PORT, None
    )
    assert bastion_user == BASTION_USER


def test_manage_conf_file_bastion_all_undefined():
    write_conf_file(BASTION_CONF_FILE)
    bastion_host, bastion_port, bastion_user = manage_conf_file(
        BASTION_CONF_FILE, None, None, None
    )
    assert bastion_user == BASTION_USER
    assert bastion_port == BASTION_PORT
    assert bastion_host == BASTION_HOST


def write_conf_file(conf_file):
    with open(conf_file, "w") as f:

        data = {
            "bastion_host": BASTION_HOST,
            "bastion_port": BASTION_PORT,
            "bastion_user": BASTION_USER,
        }

        dump(data, f)


write_conf_file(BASTION_CONF_FILE)


def write_host_only_conf_file(conf_file):
    with open(conf_file, "w") as f:
        dump({"bastion_host": BASTION_HOST}, f)


write_host_only_conf_file(BASTION_HOST_ONLY_CONF_FILE)


def test_get_var_within_one_level():
    hostvars = {"bastion_host": "{{ bastion_fqdn }}", "bastion_fqdn": "my_real_bastion"}
    bastion_host = get_var_within(hostvars["bastion_host"], hostvars)
    assert bastion_host == hostvars["bastion_fqdn"]


def test_get_var_within_two_levels():
    hostvars = {
        "bastion_host": "{{ bastion_fqdn }}",
        "bastion_fqdn": "{{ my_other_var }}",
        "my_other_var": "my_real_bastion",
    }
    bastion_host = get_var_within(hostvars["bastion_host"], hostvars)
    assert bastion_host == hostvars["my_other_var"]


def test_get_var_within_not_found():
    hostvars = {"bastion_host": "{{ bastion_fqdn }}"}
    bastion_host = get_var_within(hostvars["bastion_host"], hostvars)
    assert not bastion_host


def test_get_var_within_infinite():
    hostvars = {
        "bastion_host": "{{ bastion_fqdn }}",
        "bastion_fqdn": "{{ bastion_host }}",
    }
    bastion_host = get_var_within(hostvars["bastion_host"], hostvars)
    assert not bastion_host


def test_get_var_not_a_jinja2_var():
    hostvars = {"bastion_host": "{{ bastion_fqdn"}
    bastion_host = get_var_within(hostvars["bastion_host"], hostvars)
    assert bastion_host == hostvars["bastion_host"]


def test_get_var_not_a_string():
    hostvars = {"bastion_host": 68}
    bastion_host = get_var_within(hostvars["bastion_host"], hostvars)
    assert bastion_host == hostvars["bastion_host"]


def test_awx_get_inventory_file_default():
    assert awx_get_inventory_file() == "/runner/inventory/hosts"


def test_awx_get_inventory_file_env_defined():
    env_path = "/my_awx"
    os.environ["AWX_RUN_DIR"] = env_path
    assert awx_get_inventory_file() == f"{env_path}/inventory/hosts"
    os.environ.pop("AWX_RUN_DIR")


AWX_HOST = "serv01.example.net"
AWX_INVENTORY_FILE = "/runner/inventory/hosts"
AWX_BASTION_VARS = {
    "bastion_host": BASTION_HOST,
    "bastion_port": BASTION_PORT,
    "bastion_user": BASTION_USER,
}


def fake_awx_inventory(monkeypatch, hostvars, host_lookup=None):
    """Answer the AWX inventory script, then the per-host lookup it may run"""
    commands = []

    def fake_get_inv_from_command(command):
        commands.append(command)
        if command == AWX_INVENTORY_FILE:
            return {"_meta": {"hostvars": hostvars}}
        return host_lookup or {}

    monkeypatch.setattr(lib, "get_inv_from_command", fake_get_inv_from_command)
    return commands


def test_awx_get_vars_without_ansible_host(monkeypatch):
    fake_awx_inventory(monkeypatch, {AWX_HOST: dict(AWX_BASTION_VARS)})
    assert awx_get_vars(AWX_HOST, AWX_INVENTORY_FILE) == AWX_BASTION_VARS


def test_awx_get_vars_with_ansible_host(monkeypatch):
    hostvars = dict(AWX_BASTION_VARS, ansible_host="10.0.0.1")
    fake_awx_inventory(monkeypatch, {AWX_HOST: hostvars})
    assert awx_get_vars("10.0.0.1", AWX_INVENTORY_FILE) == AWX_BASTION_VARS


def test_awx_get_vars_without_ansible_host_falls_back_to_group_vars(monkeypatch):
    commands = fake_awx_inventory(
        monkeypatch,
        {AWX_HOST: {"bastion_host": BASTION_HOST}},
        host_lookup=AWX_BASTION_VARS,
    )
    assert awx_get_vars(AWX_HOST, AWX_INVENTORY_FILE) == AWX_BASTION_VARS
    assert commands[-1] == "ansible-inventory -i {} --host {}".format(
        AWX_INVENTORY_FILE, AWX_HOST
    )


def test_awx_get_vars_of_an_unknown_host(monkeypatch):
    fake_awx_inventory(monkeypatch, {AWX_HOST: dict(AWX_BASTION_VARS)})
    assert awx_get_vars("serv99.example.net", AWX_INVENTORY_FILE) == {}


def test_get_bastion_vars():
    host_vars = {
        "bastion_port": BASTION_PORT,
        "bastion_host": BASTION_HOST,
        "bastion_user": BASTION_USER,
    }
    bastion_vars = get_bastion_vars(host_vars)
    assert (
        bastion_vars["bastion_port"] == BASTION_PORT
        and bastion_vars["bastion_host"] == BASTION_HOST
        and bastion_vars["bastion_user"] == BASTION_USER
    )


def test_get_bastion_vars_not_full():
    host_vars = {"bastion_port": BASTION_PORT, "bastion_user": BASTION_USER}
    bastion_vars = get_bastion_vars(host_vars)
    assert not bastion_vars["bastion_host"]


def test_parse_bastion_env_vars():
    bastion_host, bastion_port, bastion_user = parse_bastion_env_vars(AWX_COMMAND)
    assert bastion_host == BASTION_HOST
    assert bastion_port == str(BASTION_PORT)
    assert bastion_user == BASTION_USER


def test_parse_bastion_env_vars_not_defined():
    bastion_host, bastion_port, bastion_user = parse_bastion_env_vars(
        "/bin/sh -c '/usr/bin/python3 && sleep 0'"
    )
    assert not bastion_host
    assert not bastion_port
    assert not bastion_user


def test_parse_bastion_env_vars_token_without_value():
    bastion_host, _, _ = parse_bastion_env_vars(BASTION_PATH_COMMAND)
    assert bastion_host == BASTION_HOST


def test_parse_bastion_env_vars_empty_value():
    bastion_host, _, _ = parse_bastion_env_vars("/bin/sh -c 'BASTION_HOST= /bin/true'")
    assert not bastion_host


def test_parse_bastion_env_vars_value_with_equal_sign():
    bastion_host, _, _ = parse_bastion_env_vars(
        "/bin/sh -c 'BASTION_HOST=host=1 /bin/true'"
    )
    assert bastion_host == "host=1"


def test_parse_bastion_env_vars_ignores_match_on_value_only():
    bastion_host, _, _ = parse_bastion_env_vars(
        "BASTION_ANSIBLE_INV_OPTIONS=-i /etc/ansible/bastion_hosts.yml"
    )
    assert not bastion_host


def test_parse_ssh_argv_option_value_is_not_a_host():
    options, letters, remaining = parse_ssh_argv(["-o", "User=deploy", "127.0.0.1"])
    assert options == ["-o", "User=deploy"]
    assert letters == {"o"}
    assert remaining == ["127.0.0.1"]


def test_parse_ssh_argv_attached_option_value():
    options, _, remaining = parse_ssh_argv(["-oForwardX11 no", "127.0.0.1", "uptime"])
    assert options == ["-oForwardX11 no"]
    assert remaining == ["127.0.0.1", "uptime"]


def test_parse_ssh_argv_bundled_options():
    options, letters, remaining = parse_ssh_argv(["-Cq", "-tt", "127.0.0.1", "uptime"])
    assert options == ["-Cq", "-tt"]
    assert letters == {"C", "q", "t"}
    assert remaining == ["127.0.0.1", "uptime"]


def test_parse_ssh_argv_end_of_options():
    options, _, remaining = parse_ssh_argv(["-q", "--", "127.0.0.1", "uptime"])
    assert options == ["-q", "--"]
    assert remaining == ["127.0.0.1", "uptime"]


def test_find_executable_from_path():
    assert find_executable("sh", path="/nonexistent:/bin:/usr/bin").endswith("/sh")


def test_find_executable_not_executable():
    assert not find_executable("hosts", path="/etc")


def test_find_executable_not_found():
    assert not find_executable("no_such_executable", path="/bin:/usr/bin")


def test_parse_bastion_env_vars_variable_name_ending_with_a_bastion_var():
    bastion_host, _, bastion_user = parse_bastion_env_vars(
        "NO_BASTION_HOST=1 SKIP_BASTION_USER=true /usr/bin/python3"
    )
    assert not bastion_host
    assert not bastion_user


def test_parse_bastion_env_vars_first_assignment_wins():
    bastion_host, _, _ = parse_bastion_env_vars(
        "BASTION_HOST=first BASTION_HOST=second /usr/bin/python3"
    )
    assert bastion_host == "first"


def test_parse_bastion_env_vars_ignores_an_assignment_inside_a_command():
    bastion_host, _, _ = parse_bastion_env_vars("/bin/sh -c 'echo bastion_host=evil'")
    assert not bastion_host


def test_parse_bastion_env_vars_ignores_an_assignment_in_an_argument():
    bastion_host, _, _ = parse_bastion_env_vars(
        "/bin/sh -c 'echo \"BASTION_HOST=evil and more\"'"
    )
    assert not bastion_host


def test_parse_bastion_env_vars_quoted_value():
    bastion_host, _, bastion_user = parse_bastion_env_vars(
        "BASTION_HOST=\"my_bastion\" BASTION_USER='my user' /bin/true"
    )
    assert bastion_host == "my_bastion"
    assert bastion_user == "my user"


def test_has_remote_command():
    assert has_remote_command(["-o", "User=deploy", "127.0.0.1", AWX_COMMAND])


def test_has_remote_command_control_master_check():
    assert not has_remote_command(
        ["-o", "ControlPath=/tmp/cp", "-O", "check", "myhost"]
    )


def test_has_remote_command_control_master_operation():
    assert not has_remote_command(["-o", "ControlPath=/tmp/cp", "-O", "stop", "myhost"])


def test_has_remote_command_version_probe():
    assert not has_remote_command(["-V"])


def test_has_remote_command_no_argument():
    assert not has_remote_command([])


def test_has_remote_command_option_before_host():
    assert not has_remote_command(["-G", "myhost"])


def test_has_remote_command_option_value_looking_like_a_host():
    assert not has_remote_command(["-o", "User=deploy", "127.0.0.1"])
    assert not has_remote_command(["-C", "-o", "ControlMaster=auto", "127.0.0.1"])


def test_has_remote_command_control_master_option_value():
    # -W takes a value, the -O here is not a control master operation
    assert has_remote_command(["-W", "-O", "127.0.0.1", "/bin/sh -c true"])


def test_has_remote_command_control_master_as_the_command():
    assert has_remote_command(["-o", "User=deploy", "127.0.0.1", "-O"])


def test_sshwrapper_bastion_vars_from_awx_command():
    args = exec_wrapper(sshwrapper, SSH_ARGS + [AWX_COMMAND])
    assert args[:10] == [
        "ssh",
        "-p",
        str(BASTION_PORT),
        "-q",
        "-o",
        "StrictHostKeyChecking=no",
        "-l",
        BASTION_USER,
        BASTION_HOST,
        "-T",
    ]
    assert args[-7:] == [
        "--user",
        "deploy",
        "--port",
        "2222",
        "127.0.0.1",
        "--",
        AWX_COMMAND,
    ]


def test_sshwrapper_command_holding_a_bastion_path():
    args = exec_wrapper(sshwrapper, SSH_ARGS + [BASTION_PATH_COMMAND])
    assert BASTION_HOST in args
    assert args[-1] == BASTION_PATH_COMMAND


def test_sshwrapper_passes_through_control_master_operation():
    argv = ["-o", "ControlPath=/tmp/cp", "-O", "stop", "127.0.0.1"]
    assert exec_wrapper(sshwrapper, argv) == ["ssh"] + argv


def test_sshwrapper_passes_through_version_probe():
    assert exec_wrapper(sshwrapper, ["-V"]) == ["ssh", "-V"]


def test_scpwrapper_command_holding_a_bastion_path():
    args = exec_wrapper(scpwrapper, SCP_ARGS, conf_file=BASTION_CONF_FILE)
    assert args == [
        "ssh",
        "{}@{}".format(BASTION_USER, BASTION_HOST),
        "-p",
        str(BASTION_PORT),
        "-o",
        "StrictHostKeyChecking=no",
        "-T",
        "-x",
        "-oPermitLocalCommand=no",
        "--",
        "--user",
        "deploy",
        "--port",
        "22",
        "--host",
        "127.0.0.1",
        "--osh",
        "scp",
        "--scp-cmd",
        "scp#-t#/etc/ansible/bastion_hosts.yml",
    ]


def test_scpwrapper_bastion_vars_from_command():
    command = "BASTION_USER={} BASTION_HOST={} BASTION_PORT={} {}".format(
        BASTION_USER, BASTION_HOST, BASTION_PORT, SCP_COMMAND
    )
    args = exec_wrapper(scpwrapper, SCP_ARGS[:-1] + [command])
    assert args[1] == "{}@{}".format(BASTION_USER, BASTION_HOST)
    assert args[3] == str(BASTION_PORT)


def test_scpwrapper_escapes_the_command():
    args = exec_wrapper(
        scpwrapper, SCP_ARGS[:-1] + ["scp -t /tmp/a b#c"], conf_file=BASTION_CONF_FILE
    )
    assert args[-1] == "scp#-t#/tmp/a#b##c"


def test_sftpwrapper_forwards_the_ssh_options():
    args = exec_wrapper(sftpwrapper, SFTP_ARGS, conf_file=BASTION_CONF_FILE)
    assert args == [
        "ssh",
        "{}@{}".format(BASTION_USER, BASTION_HOST),
        "-p",
        str(BASTION_PORT),
        "-o",
        "StrictHostKeyChecking=no",
        "-T",
        "-oForwardX11 no",
        "-oControlMaster no",
        "-o",
        "IdentityFile=/tmp/id_ansible",
        "--",
        "--user",
        "deploy",
        "--port",
        "2222",
        "--host",
        "127.0.0.1",
        "--osh",
        "sftp",
    ]


def test_scpwrapper_trailing_option_without_a_value():
    args = exec_wrapper(scpwrapper, SCP_ARGS + ["-l"], conf_file=BASTION_CONF_FILE)
    assert args[-1] == "-l"


def test_sftpwrapper_trailing_option_without_a_value():
    args = exec_wrapper(sftpwrapper, ["-o"] + SFTP_ARGS, conf_file=BASTION_CONF_FILE)
    assert "-o" in args


def test_sshwrapper_without_an_ssh_executable():
    with pytest.raises(SystemExit):
        exec_wrapper(sshwrapper, SSH_ARGS + [AWX_COMMAND], path="/nonexistent")


def test_scpwrapper_without_an_ssh_executable():
    with pytest.raises(SystemExit):
        exec_wrapper(scpwrapper, SCP_ARGS, path="/nonexistent")


def test_sftpwrapper_without_an_ssh_executable():
    with pytest.raises(SystemExit):
        exec_wrapper(sftpwrapper, SFTP_ARGS, path="/nonexistent")


def test_scpwrapper_without_a_command_to_proxy():
    with pytest.raises(SystemExit):
        exec_wrapper(scpwrapper, ["-x", "--"])


def test_sftpwrapper_without_a_host_to_proxy():
    with pytest.raises(SystemExit):
        exec_wrapper(sftpwrapper, ["-s"])


def test_check_bastion_host_defined():
    check_bastion_host(BASTION_HOST)


def test_check_bastion_host_undefined():
    with pytest.raises(SystemExit) as excinfo:
        check_bastion_host(None)
    assert str(excinfo.value) == (
        "bastion wrapper: no bastion host found for this connection"
    )


def test_check_bastion_host_empty():
    with pytest.raises(SystemExit):
        check_bastion_host("")


def test_sshwrapper_without_a_bastion_host(monkeypatch):
    monkeypatch.delenv("BASTION_HOST", raising=False)
    monkeypatch.setattr(sshwrapper, "get_hostvars", lambda host: {})
    with pytest.raises(SystemExit):
        exec_wrapper(sshwrapper, SSH_ARGS + ["/bin/sh -c 'true'"])


def test_scpwrapper_without_a_bastion_host(monkeypatch):
    monkeypatch.delenv("BASTION_HOST", raising=False)
    monkeypatch.setattr(scpwrapper, "get_hostvars", lambda host: {})
    with pytest.raises(SystemExit):
        exec_wrapper(scpwrapper, SCP_ARGS)


def test_sftpwrapper_without_a_bastion_host(monkeypatch):
    monkeypatch.delenv("BASTION_HOST", raising=False)
    monkeypatch.setattr(sftpwrapper, "get_hostvars", lambda host: {})
    with pytest.raises(SystemExit):
        exec_wrapper(sftpwrapper, SFTP_ARGS)


def test_fill_bastion_vars_keeps_the_values_it_is_given():
    bastion_host, bastion_port, bastion_user = fill_bastion_vars(
        {"bastion_host": "from_inventory", "bastion_port": 2222, "bastion_user": "inv"},
        BASTION_HOST,
        BASTION_PORT,
        BASTION_USER,
    )
    assert bastion_host == BASTION_HOST
    assert bastion_port == BASTION_PORT
    assert bastion_user == BASTION_USER


def test_fill_bastion_vars_fills_the_missing_ones():
    hostvar = {"bastion_host": "from_inventory", "bastion_port": 2222}
    bastion_host, bastion_port, bastion_user = fill_bastion_vars(
        hostvar, None, None, BASTION_USER
    )
    assert bastion_host == "from_inventory"
    assert bastion_port == 2222
    assert bastion_user == BASTION_USER


def test_fill_bastion_vars_resolves_a_jinja_var():
    hostvar = {"bastion_host": "{{ my_bastion }}", "my_bastion": BASTION_HOST}
    bastion_host, _, _ = fill_bastion_vars(hostvar, None, BASTION_PORT, BASTION_USER)
    assert bastion_host == BASTION_HOST


def test_sshwrapper_keeps_the_host_of_the_configuration_file(monkeypatch):
    monkeypatch.delenv("BASTION_HOST", raising=False)
    monkeypatch.setattr(sshwrapper, "get_hostvars", lambda host: {})
    args = exec_wrapper(
        sshwrapper,
        SSH_ARGS + ["/bin/sh -c 'true'"],
        conf_file=BASTION_HOST_ONLY_CONF_FILE,
    )
    assert BASTION_HOST in args


def test_sshwrapper_keeps_the_vars_of_the_command(monkeypatch):
    monkeypatch.delenv("BASTION_HOST", raising=False)
    monkeypatch.setattr(sshwrapper, "get_hostvars", lambda host: {})
    command = "/bin/sh -c 'BASTION_HOST={} BASTION_USER={} /usr/bin/python3'".format(
        BASTION_HOST, BASTION_USER
    )
    args = exec_wrapper(sshwrapper, SSH_ARGS + [command])
    assert args[:9] == [
        "ssh",
        "-p",
        str(BASTION_PORT),
        "-q",
        "-o",
        "StrictHostKeyChecking=no",
        "-l",
        BASTION_USER,
        BASTION_HOST,
    ]


def test_scpwrapper_keeps_the_host_of_the_configuration_file(monkeypatch):
    monkeypatch.delenv("BASTION_HOST", raising=False)
    monkeypatch.setattr(scpwrapper, "get_hostvars", lambda host: {})
    args = exec_wrapper(scpwrapper, SCP_ARGS, conf_file=BASTION_HOST_ONLY_CONF_FILE)
    assert args[1].endswith("@{}".format(BASTION_HOST))


def test_sftpwrapper_keeps_the_host_of_the_configuration_file(monkeypatch):
    monkeypatch.delenv("BASTION_HOST", raising=False)
    monkeypatch.setattr(sftpwrapper, "get_hostvars", lambda host: {})
    args = exec_wrapper(sftpwrapper, SFTP_ARGS, conf_file=BASTION_HOST_ONLY_CONF_FILE)
    assert args[1].endswith("@{}".format(BASTION_HOST))


INVENTORY = {"_meta": {"hostvars": {"target": {"bastion_host": BASTION_HOST}}}}


def count_inventory_commands(monkeypatch, tmp_path):
    """Make the inventory command countable and cached in a file of its own"""
    commands = []

    def fake_get_inv_from_command(command):
        commands.append(command)
        return INVENTORY

    monkeypatch.setattr(lib, "get_inv_from_command", fake_get_inv_from_command)
    monkeypatch.setattr(lib, "find_executable", lambda e, path=None: "/usr/bin/" + e)
    monkeypatch.setenv("BASTION_ANSIBLE_INV_CACHE_FILE", str(tmp_path / "cache.json"))
    monkeypatch.delenv("BASTION_ANSIBLE_INV_OPTIONS", raising=False)
    monkeypatch.delenv("ANSIBLE_INVENTORY", raising=False)
    monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
    return commands


def test_get_inventory_runs_the_command_once_for_the_same_source(monkeypatch, tmp_path):
    commands = count_inventory_commands(monkeypatch, tmp_path)
    assert lib.get_inventory() == INVENTORY
    assert lib.get_inventory() == INVENTORY
    assert len(commands) == 1


def test_get_inventory_runs_the_command_again_for_another_option(monkeypatch, tmp_path):
    commands = count_inventory_commands(monkeypatch, tmp_path)
    monkeypatch.setenv("BASTION_ANSIBLE_INV_OPTIONS", "-i first.yml")
    lib.get_inventory()
    monkeypatch.setenv("BASTION_ANSIBLE_INV_OPTIONS", "-i second.yml")
    lib.get_inventory()
    assert len(commands) == 2


def test_get_inventory_runs_the_command_again_for_another_inventory(
    monkeypatch, tmp_path
):
    commands = count_inventory_commands(monkeypatch, tmp_path)
    monkeypatch.setenv("ANSIBLE_INVENTORY", "first.yml")
    lib.get_inventory()
    monkeypatch.setenv("ANSIBLE_INVENTORY", "second.yml")
    lib.get_inventory()
    assert len(commands) == 2


def test_get_inventory_runs_the_command_again_for_another_config(monkeypatch, tmp_path):
    commands = count_inventory_commands(monkeypatch, tmp_path)
    monkeypatch.setenv("ANSIBLE_CONFIG", "first.cfg")
    lib.get_inventory()
    monkeypatch.setenv("ANSIBLE_CONFIG", "second.cfg")
    lib.get_inventory()
    assert len(commands) == 2


def test_get_inventory_runs_the_command_once_per_call_without_a_cache_file(
    monkeypatch, tmp_path
):
    commands = count_inventory_commands(monkeypatch, tmp_path)
    monkeypatch.delenv("BASTION_ANSIBLE_INV_CACHE_FILE")
    lib.get_inventory()
    lib.get_inventory()
    assert len(commands) == 2


def count_lookups(monkeypatch):
    """Count what a wrapper reads outside of its own arguments"""
    lookups = {"inventory": [], "conf_file": []}

    monkeypatch.setattr(
        lib,
        "get_inv_from_command",
        lambda command: (lookups["inventory"].append(command), INVENTORY)[1],
    )

    real_manage_conf_file = lib.manage_conf_file

    def counting_manage_conf_file(conf_file, *args):
        lookups["conf_file"].append(conf_file)
        return real_manage_conf_file(conf_file, *args)

    for wrapper in (sshwrapper, scpwrapper, sftpwrapper):
        monkeypatch.setattr(wrapper, "manage_conf_file", counting_manage_conf_file)
        monkeypatch.setattr(wrapper, "get_hostvars", lib.get_hostvars)

    return lookups


def test_sshwrapper_reads_nothing_for_a_local_operation(monkeypatch):
    lookups = count_lookups(monkeypatch)
    exec_wrapper(sshwrapper, ["-V"])
    assert lookups == {"inventory": [], "conf_file": []}


def test_sshwrapper_reads_nothing_when_the_command_carries_the_vars(monkeypatch):
    lookups = count_lookups(monkeypatch)
    exec_wrapper(sshwrapper, SSH_ARGS + [AWX_COMMAND])
    assert lookups == {"inventory": [], "conf_file": []}


def test_sshwrapper_skips_the_inventory_for_a_complete_configuration_file(monkeypatch):
    lookups = count_lookups(monkeypatch)
    exec_wrapper(
        sshwrapper, SSH_ARGS + ["/bin/sh -c true"], conf_file=BASTION_CONF_FILE
    )
    assert lookups["conf_file"] == [BASTION_CONF_FILE]
    assert lookups["inventory"] == []


def test_scpwrapper_skips_the_inventory_for_a_complete_configuration_file(monkeypatch):
    lookups = count_lookups(monkeypatch)
    exec_wrapper(scpwrapper, SCP_ARGS, conf_file=BASTION_CONF_FILE)
    assert lookups["conf_file"] == [BASTION_CONF_FILE]
    assert lookups["inventory"] == []


def test_sftpwrapper_skips_the_inventory_for_a_complete_configuration_file(monkeypatch):
    lookups = count_lookups(monkeypatch)
    exec_wrapper(sftpwrapper, SFTP_ARGS, conf_file=BASTION_CONF_FILE)
    assert lookups["conf_file"] == [BASTION_CONF_FILE]
    assert lookups["inventory"] == []


def test_sshwrapper_lists_the_inventory_once_for_a_missing_var(monkeypatch):
    lookups = count_lookups(monkeypatch)
    monkeypatch.delenv("BASTION_ANSIBLE_INV_CACHE_FILE", raising=False)
    monkeypatch.setattr(lib, "find_executable", lambda e, path=None: "/usr/bin/" + e)
    exec_wrapper(
        sshwrapper,
        SSH_ARGS + ["/bin/sh -c true"],
        conf_file=BASTION_HOST_ONLY_CONF_FILE,
    )
    assert len(lookups["inventory"]) == 1
    assert len(lookups["conf_file"]) == 1


def test_source_enabled_by_default(monkeypatch):
    monkeypatch.delenv("BASTION_TEST_SOURCE_ENABLED", raising=False)
    assert source_enabled("BASTION_TEST_SOURCE_ENABLED")


@pytest.mark.parametrize("value", ["1", "yes", "true", "on", "anything"])
def test_source_enabled_values(monkeypatch, value):
    monkeypatch.setenv("BASTION_TEST_SOURCE_ENABLED", value)
    assert source_enabled("BASTION_TEST_SOURCE_ENABLED")


@pytest.mark.parametrize("value", ["0", "no", "false", "off", "FALSE", " 0 "])
def test_source_disabled_values(monkeypatch, value):
    monkeypatch.setenv("BASTION_TEST_SOURCE_ENABLED", value)
    assert not source_enabled("BASTION_TEST_SOURCE_ENABLED")


def test_sshwrapper_playbook_env_source_disabled(monkeypatch):
    lookups = count_lookups(monkeypatch)
    monkeypatch.setenv("BASTION_PLAYBOOK_ENV_ENABLED", "0")
    monkeypatch.setenv("BASTION_ANSIBLE_INVENTORY_ENABLED", "0")
    monkeypatch.delenv("BASTION_HOST", raising=False)
    with pytest.raises(SystemExit):
        exec_wrapper(sshwrapper, SSH_ARGS + [AWX_COMMAND])
    assert lookups["inventory"] == []


def test_scpwrapper_playbook_env_source_disabled(monkeypatch):
    lookups = count_lookups(monkeypatch)
    monkeypatch.setenv("BASTION_PLAYBOOK_ENV_ENABLED", "0")
    monkeypatch.setenv("BASTION_ANSIBLE_INVENTORY_ENABLED", "0")
    monkeypatch.delenv("BASTION_HOST", raising=False)
    command = "scp -t BASTION_HOST={}".format(BASTION_HOST)
    with pytest.raises(SystemExit):
        exec_wrapper(scpwrapper, SCP_ARGS[:-1] + [command])
    assert lookups["inventory"] == []


def test_sshwrapper_conf_file_source_disabled(monkeypatch):
    lookups = count_lookups(monkeypatch)
    monkeypatch.setenv("BASTION_CONF_FILE_ENABLED", "0")
    monkeypatch.setenv("BASTION_ANSIBLE_INVENTORY_ENABLED", "0")
    monkeypatch.delenv("BASTION_HOST", raising=False)
    with pytest.raises(SystemExit):
        exec_wrapper(
            sshwrapper, SSH_ARGS + ["/bin/sh -c true"], conf_file=BASTION_CONF_FILE
        )
    assert lookups["conf_file"] == []


def test_sftpwrapper_conf_file_source_disabled(monkeypatch):
    lookups = count_lookups(monkeypatch)
    monkeypatch.setenv("BASTION_CONF_FILE_ENABLED", "0")
    monkeypatch.setenv("BASTION_ANSIBLE_INVENTORY_ENABLED", "0")
    monkeypatch.delenv("BASTION_HOST", raising=False)
    with pytest.raises(SystemExit):
        exec_wrapper(sftpwrapper, SFTP_ARGS, conf_file=BASTION_CONF_FILE)
    assert lookups["conf_file"] == []


def test_sshwrapper_inventory_source_disabled(monkeypatch):
    lookups = count_lookups(monkeypatch)
    monkeypatch.setenv("BASTION_ANSIBLE_INVENTORY_ENABLED", "0")
    monkeypatch.setenv("BASTION_HOST", BASTION_HOST)
    args = exec_wrapper(sshwrapper, SSH_ARGS + ["/bin/sh -c true"])
    assert lookups["inventory"] == []
    assert BASTION_HOST in args


def test_scpwrapper_inventory_source_disabled(monkeypatch):
    lookups = count_lookups(monkeypatch)
    monkeypatch.setenv("BASTION_ANSIBLE_INVENTORY_ENABLED", "0")
    monkeypatch.setenv("BASTION_HOST", BASTION_HOST)
    args = exec_wrapper(scpwrapper, SCP_ARGS)
    assert lookups["inventory"] == []
    assert args[1].endswith("@{}".format(BASTION_HOST))


def test_sftpwrapper_inventory_source_disabled(monkeypatch):
    lookups = count_lookups(monkeypatch)
    monkeypatch.setenv("BASTION_ANSIBLE_INVENTORY_ENABLED", "0")
    monkeypatch.setenv("BASTION_HOST", BASTION_HOST)
    args = exec_wrapper(sftpwrapper, SFTP_ARGS)
    assert lookups["inventory"] == []
    assert args[1].endswith("@{}".format(BASTION_HOST))


def test_os_env_source_disabled(monkeypatch):
    monkeypatch.setenv("BASTION_OS_ENV_ENABLED", "0")
    monkeypatch.setenv("BASTION_HOST", BASTION_HOST)
    monkeypatch.setenv("BASTION_PORT", "2222")
    monkeypatch.setenv("BASTION_USER", "from_env")
    bastion_host, bastion_port, bastion_user = fill_bastion_vars({}, None, None, None)
    assert bastion_host is None
    assert bastion_port == 22
    assert bastion_user != "from_env"


def test_os_env_source_enabled_by_default(monkeypatch):
    monkeypatch.delenv("BASTION_OS_ENV_ENABLED", raising=False)
    monkeypatch.setenv("BASTION_HOST", BASTION_HOST)
    monkeypatch.setenv("BASTION_PORT", "2222")
    monkeypatch.setenv("BASTION_USER", "from_env")
    bastion_host, bastion_port, bastion_user = fill_bastion_vars({}, None, None, None)
    assert (bastion_host, bastion_port, bastion_user) == (
        BASTION_HOST,
        "2222",
        "from_env",
    )

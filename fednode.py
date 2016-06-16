#!/usr/bin/env python3
'''
fednode.py: script to manage a Counterparty federated node
'''

import sys
import os
import re
import argparse
import copy
import subprocess
import configparser
import socket
import glob
import shutil

CURDIR = os.getcwd()
SCRIPTDIR = os.path.dirname(os.path.realpath(__file__))
FEDNODE_CONFIG_FILE = ".fednode.config"
FEDNODE_CONFIG_PATH = os.path.join(SCRIPTDIR, FEDNODE_CONFIG_FILE)

PROJECT_NAME = "federatednode"

REPO_BASE_HTTPS = "https://github.com/CounterpartyXCP/{}.git"
REPO_BASE_SSH = "git@github.com:CounterpartyXCP/{}.git"
REPOS_BASE = ['counterparty-lib', 'counterparty-cli']
REPOS_COUNTERBLOCK = REPOS_BASE + ['counterblock', ]
REPOS_FULL = REPOS_COUNTERBLOCK + ['counterwallet', 'armory-utxsvr']

HOST_PORTS_USED = {
    'base': [8332, 18332, 4000, 14000],
    'counterblock': [8332, 18332, 4000, 14000, 4100, 14100],
    'full': [8332, 18332, 4000, 14000, 4100, 14100, 80, 443]
}
UPDATE_CHOICES = ['counterparty', 'counterparty-testnet', 'counterblock', 'counterblock-testnet', 'counterwallet', 'armory-utxsvr', 'armory-utxsvr-testnet']
REPARSE_CHOICES = ['counterparty', 'counterparty-testnet', 'counterblock', 'counterblock-testnet']

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action='store_true', default=False, help="increase output verbosity")

    subparsers = parser.add_subparsers(help='help on modes', dest='command')
    subparsers.required = True

    parser_install = subparsers.add_parser('install', help="install fednode services")
    parser_install.add_argument("config", choices=['base', 'counterblock', 'full'], help="The name of the service configuration to utilize")
    parser_install.add_argument("branch", choices=['master', 'develop'], help="The name of the git branch to utilize for the build")
    parser_install.add_argument("--use-ssh-uris", action="store_true", help="Use SSH URIs for source checkouts from Github, instead of HTTPS URIs")

    parser_uninstall = subparsers.add_parser('uninstall', help="uninstall fednode services")

    parser_start = subparsers.add_parser('start', help="start fednode services")
    parser_start.add_argument("services", nargs='*', default='', help="The service or services to start (or blank for all services)")

    parser_stop = subparsers.add_parser('stop', help="stop fednode services")
    parser_stop.add_argument("services", nargs='*', default='', help="The service or services to stop (or blank for all services)")

    parser_restart = subparsers.add_parser('restart', help="restart fednode services")
    parser_restart.add_argument("services", nargs='*', default='', help="The service or services to restart (or blank for all services)")

    parser_reparse = subparsers.add_parser('reparse', help="reparse a counterparty-server or counterblock service")
    parser_reparse.add_argument("service", choices=REPARSE_CHOICES, help="The name of the service for which to kick off a reparse")

    parser_ps = subparsers.add_parser('ps', help="list installed services")

    parser_tail = subparsers.add_parser('tail', help="tail fednode logs")
    parser_tail.add_argument("services", nargs='*', default='', help="The name of the service or services whose logs to tail (or blank for all services)")

    parser_logs = subparsers.add_parser('logs', help="tail fednode logs")
    parser_logs.add_argument("services", nargs='*', default='', help="The name of the service or services whose logs to view (or blank for all services)")

    parser_exec = subparsers.add_parser('exec', help="execute a command on a specific container")
    parser_exec.add_argument("service", help="The name of the service to execute the command on")
    parser_exec.add_argument("cmd", nargs=argparse.REMAINDER, help="The shell command to execute")

    parser_shell = subparsers.add_parser('shell', help="get a shell on a specific service container")
    parser_shell.add_argument("service", help="The name of the service to shell into")

    parser_update = subparsers.add_parser('update', help="upgrade fednode services (i.e. update source code and restart the container, but don't update the container itself')")
    parser_update.add_argument("--no-restart", action="store_true", help="Don't restart the container after updating the code'")
    parser_update.add_argument("services", nargs='*', default='', help="The name of the service or services to update (or blank to for all applicable services)")

    parser_rebuild = subparsers.add_parser('rebuild', help="rebuild fednode services (i.e. remove and refetch/install docker containers)")
    parser_rebuild.add_argument("services", nargs='*', default='', help="The name of the service or services to rebuild (or blank for all services)")

    parser_docker_clean = subparsers.add_parser('docker_clean', help="remove ALL docker containers and cached images (use with caution!)")

    return parser.parse_args()


def write_config(config):
    cfg_file = open(FEDNODE_CONFIG_PATH, 'w')
    config.write(cfg_file)
    cfg_file.close()


def run_compose_cmd(docker_config_path, cmd):
    assert os.environ['FEDNODE_RELEASE_TAG']
    return os.system("docker-compose -f {} -p {} {}".format(docker_config_path, PROJECT_NAME, cmd))


def is_port_open(port):
    # TCP ports only
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    return sock.connect_ex(('127.0.0.1', port)) == 0  # returns True if the port is open


def main():
    if os.name != 'nt' and not os.geteuid() == 0:
        print("Please run this script as root")
        sys.exit(1)
    if os.name != 'nt':
        SESSION_USER = subprocess.check_output("logname", shell=True).decode("utf-8").strip()
    else:
        SESSION_USER = None

    # parse command line arguments
    args = parse_args()

    # run utility commands (docker_clean) if specified
    if args.command == 'docker_clean':
        docker_containers = subprocess.check_output("docker ps -a -q", shell=True).decode("utf-8").split('\n')
        docker_images = subprocess.check_output("docker images -q", shell=True).decode("utf-8").split('\n')
        for container in docker_containers:
            if not container:
                continue
            os.system("docker rm {}".format(container))
        for image in docker_images:
            if not image:
                continue
            os.system("docker rmi {}".format(image))
        sys.exit(1)

    # for all other commands
    # if config doesn't exist, only the 'install' command may be run
    config_existed = os.path.exists(FEDNODE_CONFIG_PATH)
    config = configparser.SafeConfigParser()
    if not config_existed:
        if args.command != 'install': 
            print("config file {} does not exist. Please run the 'install' command first".format(FEDNODE_CONFIG_FILE))
            sys.exit(1)

        # write default config
        config.add_section('Default')
        config.set('Default', 'branch', args.branch)
        config.set('Default', 'config', args.config)
        write_config(config)

    # load and read config
    assert os.path.exists(FEDNODE_CONFIG_PATH)
    config.read(FEDNODE_CONFIG_PATH)
    build_config = config.get('Default', 'config')
    docker_config_file = "docker-compose.{}.yml".format(build_config)
    docker_config_path = os.path.join(SCRIPTDIR, docker_config_file)
    repo_branch = config.get('Default', 'branch')
    os.environ['FEDNODE_RELEASE_TAG'] = config.get('Default', 'branch')
    os.environ['HOSTNAME_BASE'] = socket.gethostname()

    # perform action for the specified command
    if args.command == 'install':
        if config_existed:
            print("Cannot install, as it appears a configuration already exists. Please run the 'uninstall' command first")
            sys.exit(1)

        # check port usage
        for port in HOST_PORTS_USED[build_config]:
            if is_port_open(port):
                print("Cannot install, as it appears a process is already listening on host port {}".format(port))
                sys.exit(1)

        # check out the necessary source trees (don't use submodules due to detached HEAD and other problems)
        REPOS = REPOS_BASE if build_config == 'base' else (REPOS_COUNTERBLOCK if build_config == 'counterblock' else REPOS_FULL)
        for repo in REPOS:
            repo_url = REPO_BASE_SSH.format(repo) if args.use_ssh_uris else REPO_BASE_HTTPS.format(repo)
            repo_dir = os.path.join(SCRIPTDIR, "src", repo)
            if not os.path.exists(repo_dir):
                git_cmd = "git clone -b {} {} {}".format(repo_branch, repo_url, repo_dir)
                if SESSION_USER:  # check out the code as the original user, so the permissions are right
                    os.system("sudo -u {} bash -c \"{}\"".format(SESSION_USER, git_cmd))
                else:
                    os.system(git_cmd)

        # make sure we have the newest image for each service
        run_compose_cmd(docker_config_path, "pull --ignore-pull-failures")

        # copy over the configs from .default to active versions, if they don't already exist
        for default_config in glob.iglob(os.path.join(SCRIPTDIR, 'config', '**/*.default'), recursive=True):
            active_config = default_config.replace('.default', '')
            if not os.path.exists(active_config):
                print("Generating config from defaults at {} ...".format(active_config))
                shutil.copy2(default_config, active_config)
                default_config_stat = os.stat(default_config)
                os.chown(active_config, default_config_stat.st_uid, default_config_stat.st_gid)

        # launch
        run_compose_cmd(docker_config_path, "up -d")
    elif args.command == 'uninstall':
        run_compose_cmd(docker_config_path, "down")
        os.remove(FEDNODE_CONFIG_PATH)
    elif args.command == 'start':
        run_compose_cmd(docker_config_path, "start {}".format(' '.join(args.services)))
    elif args.command == 'stop':
        run_compose_cmd(docker_config_path, "stop {}".format(' '.join(args.services)))
    elif args.command == 'restart':
        run_compose_cmd(docker_config_path, "restart {}".format(' '.join(args.services)))
    elif args.command == 'reparse':
        run_compose_cmd(docker_config_path, "stop {}".format(args.service))
        if args.service in ['counterparty', 'counterparty-testnet']:
            run_compose_cmd(docker_config_path, "run -e COMMAND=reparse {}".format(args.service))
        elif args.service in ['counterblock', 'counterblock-testnet']:
            run_compose_cmd(docker_config_path, "run -e EXTRA_PARAMS=\"--reparse\" {}".format(args.service))
    elif args.command == 'tail':
        run_compose_cmd(docker_config_path, "logs -f --tail=50 {}".format(' '.join(args.services)))
    elif args.command == 'logs':
        run_compose_cmd(docker_config_path, "logs {}".format(' '.join(args.services)))
    elif args.command == 'ps':
        run_compose_cmd(docker_config_path, "ps")
    elif args.command == 'exec':
        if len(args.cmd) == 1 and re.match("['\"].*?['\"]", args.cmd[0]):
            cmd = args.cmd
        else:
            cmd = '"{}"'.format(' '.join(args.cmd).replace('"', '\\"'))
        os.system("docker exec -i -t federatednode_{}_1 bash -c {}".format(args.service, cmd))
    elif args.command == 'shell':
        exec_result = os.system("docker exec -i -t federatednode_{}_1 bash".format(args.service))
        if exec_result != 0:
            print("Container is not running -- creating a transient container with a 'bash' shell entrypoint...")
            run_compose_cmd(docker_config_path, "run --no-deps --entrypoint bash {}".format(args.service))
    elif args.command == 'update':
        # validate
        if args.services != ['',]:
            for service in args.services:
                if service not in UPDATE_CHOICES:
                    print("Invalid service: {}".format(service))
                    sys.exit(1)

        services_to_update = copy.copy(UPDATE_CHOICES) if not len(args.services) else args.services
        git_has_updated = []
        while services_to_update:
            # update source code
            service = services_to_update.pop(0)
            service_base = service.replace('-testnet', '')
            if service_base not in git_has_updated:
                git_has_updated.append(service_base)
                if service_base == 'counterparty':  # special case
                    service_dirs = [os.path.join(SCRIPTDIR, "src", "counterparty-lib"), os.path.join(SCRIPTDIR, "src", "counterparty-cli")]
                else:
                    service_dirs = [service_base,]
                for service_dir in service_dirs:
                    service_dir_path = os.path.join(SCRIPTDIR, "src", service_dir)
                    service_branch = subprocess.check_output("cd {};git symbolic-ref --short -q HEAD;cd {}".format(service_dir_path, CURDIR), shell=True).decode("utf-8").strip()
                    if not service_branch:
                        print("Unknown service git branch name, or repo in detached state")
                        sys.exit(1)
                    git_cmd = "cd {}; git pull origin {}; cd {}".format(service_dir_path, service_branch, CURDIR)
                    if SESSION_USER:  # update the code as the original user, so the permissions are right
                        os.system("sudo -u {} bash -c \"{}\"".format(SESSION_USER, git_cmd))
                    else:
                        os.system(git_cmd)

            # and restart container
            if not args.no_restart:
                run_compose_cmd(docker_config_path, "restart {}".format(service))
    elif args.command == 'rebuild':
        run_compose_cmd(docker_config_path, "pull --ignore-pull-failures {}".format(' '.join(args.services)))
        run_compose_cmd(docker_config_path, "up -d --build --force-recreate --no-deps {}".format(' '.join(args.services)))


if __name__ == '__main__':
    main()

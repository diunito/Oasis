#!/usr/bin/env python3
from __future__ import annotations
import argparse
import sys
import os
import subprocess
import json
import secrets
import shutil
from datetime import datetime
import platform
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any, Union

pref = "\033["
reset = f"{pref}0m"

@dataclass
class PinEntry:
    pin: str
    profile: int

@dataclass
class Team:
    id: int
    name: str
    token: str
    wireguard_port: int
    nop: bool = False
    image: str = ""
    pins: List[PinEntry] = field(default_factory=list)

@dataclass
class Config:
    wireguard_start_port: int
    wireguard_profiles: int
    server_addr: str
    dns: str
    tick_time: int
    flag_expire_ticks: int
    initial_service_score: int
    max_flags_per_request: int
    submission_timeout: float
    network_limit_bandwidth: str
    max_vm_cpus: str
    max_vm_mem: str
    gameserver_token: str
    teams: List[Team] = field(default_factory=list)
    unsafe_privileged: bool = False
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    max_disk_size: Optional[str] = None
    gameserver_exposed_port: Optional[str] = None
    debug: bool = False
    pin_data_added: bool = False
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Config:
        teams_data = data.pop('teams', [])
        teams = [
            Team(pins=[PinEntry(**pin) for pin in team.pop('pins', [])], **team)
        for team in teams_data]
        config = cls(**data, teams=teams)
        return config
    
    @classmethod
    def from_json_file(cls, filepath: str) -> Config:
        with open(filepath, 'r') as f:
            return cls.from_dict(json.load(f))
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def save_to_file(self, filepath: str, indent: int = 4) -> None:
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=indent)

class g:
    keep_file = False
    composefile = "oasis-compose-tmp-file.yml"
    container_name = "oasis_gameserver"
    compose_project_name = "oasis"
    name = "Oasis"
    config_file = "config.json"
    prebuild_image = "oasis-prebuilder"
    prebuilded_container = "oasis-prebuilded"
    prebuilt_image = "oasis-vm-base"

use_build_on_compose = True

os.chdir(os.path.dirname(os.path.realpath(__file__)))

def is_linux():
    return "linux" in sys.platform and 'microsoft-standard' not in platform.uname().release

#Terminal colors

class colors:
    black = "30m"
    red = "31m"
    green = "32m"
    yellow = "33m"
    blue = "34m"
    magenta = "35m"
    cyan = "36m"
    white = "37m"

def dict_to_yaml(data, indent_spaces:int=4, base_indent:int=0, additional_spaces:int=0, add_text_on_dict:str|None=None):
    yaml = ''
    spaces = ' '*((indent_spaces*base_indent)+additional_spaces)
    if isinstance(data, dict):
        for key, value in data.items():
            if add_text_on_dict is not None:
                spaces_len = len(spaces)-len(add_text_on_dict)
                spaces = (' '*max(spaces_len, 0))+add_text_on_dict
                add_text_on_dict = None
            if isinstance(value, dict) or isinstance(value, list):
                yaml += f"{spaces}{key}:\n"
                yaml += dict_to_yaml(value, indent_spaces=indent_spaces, base_indent=base_indent+1, additional_spaces=additional_spaces)
            else:
                yaml += f"{spaces}{key}: {value}\n"
            spaces = ' '*((indent_spaces*base_indent)+additional_spaces)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yaml += dict_to_yaml(item, indent_spaces=indent_spaces, base_indent=base_indent, additional_spaces=additional_spaces+2, add_text_on_dict="- ")
            elif isinstance(item, list):
                yaml += dict_to_yaml(item, indent_spaces=indent_spaces, base_indent=base_indent+1, additional_spaces=additional_spaces)
            else:
                yaml += f"{spaces}- {item}\n"
    else:
        yaml += f"{data}\n"
    return yaml

def puts(text, *args, color=colors.white, is_bold=False, **kwargs):
    print(f'{pref}{1 if is_bold else 0};{color}' + text + reset, *args, **kwargs)

def sep(): puts("-----------------------------------", is_bold=True)

def cmd_check(program, get_output=False, print_output=False, no_stderr=False):
    if get_output:
        return subprocess.getoutput(program)
    if print_output:
        return subprocess.call(program, shell=True) == 0
    return subprocess.call(program, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL if no_stderr else subprocess.STDOUT, shell=True) == 0

def gen_args(args_to_parse: list[str]|None = None):                     
    
    #Main parser
    parser = argparse.ArgumentParser(description=f"{g.name} Manager")

    subcommands = parser.add_subparsers(dest="command", help="Command to execute", required=True)
    
    #Compose Command
    parser_compose = subcommands.add_parser('compose', help='Run docker compose command')
    parser_compose.add_argument('compose_args', nargs=argparse.REMAINDER, help='Arguments to pass to docker compose', default=[])
    
    #Start Command
    parser_start = subcommands.add_parser('start', help=f'Start {g.name}')
    parser_start.add_argument('--logs', required=False, action="store_true", help=f'Show {g.name} logs', default=False)
    #Gameserver options
    parser_start.add_argument('--wireguard-start-port', type=int, default=51000, help='Wireguard start port')
    parser_start.add_argument('--gameserver-token', type=str, help='Gameserver token')
    parser_start.add_argument('--max-vm-mem', type=str, default="2G", help='Max memory for VMs')
    parser_start.add_argument('--max-vm-cpus', type=str, default="1", help='Max CPUs for VMs')
    parser_start.add_argument('--wireguard-profiles', type=int, default=30, help='Number of wireguard profiles')
    parser_start.add_argument('--dns', type=str, default="1.1.1.1", help='DNS server')
    parser_start.add_argument('--server-addr', type=str, help='Oasis public ip address')
    parser_start.add_argument('--submission-timeout', type=float, default=0.03, help='Submission timeout rate limit') # 30 req/s
    parser_start.add_argument('--flag-expire-ticks', type=int, default=5, help='Flag expire ticks')
    parser_start.add_argument('--initial-service-score', type=int, default=5000, help='Initial service score')
    parser_start.add_argument('--max-flags-per-request', type=int, default=3000, help='Max flags per request')
    parser_start.add_argument('--start-time', type=str, help='Start time (RFC 3339, see https://ijmacd.github.io/rfc3339-iso8601/)')
    parser_start.add_argument('--end-time', type=str, help='End time (RFC 3339, see https://ijmacd.github.io/rfc3339-iso8601/)')
    parser_start.add_argument('--max-disk-size', type=str, default="30G", help='Max disk size for VMs')
    parser_start.add_argument('--network-limit-bandwidth', type=str, default="20mbit", help='Network limit bandwidth')
    parser_start.add_argument('--tick-time', type=int, default=120, help='Tick time in seconds')
    parser_start.add_argument('--number-of-teams', type=int, default=4, help='Number of teams')
    parser_start.add_argument('--enable-nop-team', action='store_true', help='Enable NOP team')
    #init options
    parser_start.add_argument('--privileged', '-P', action='store_true', help='Use privileged mode for VMs')
    parser_start.add_argument('--expose-gameserver', '-E', action='store_true', help='Expose gameserver port')
    parser_start.add_argument('--gameserver-port', default="127.0.0.1:8888", help='Gameserver port')
    parser_start.add_argument('--config-only', '-C', action='store_true', help='Only generate config file')
    parser_start.add_argument('--disk-limit', '-D', action='store_true', help='Limit disk size for VMs (NEED TO ENABLE QUOTAS)')

    #Stop Command
    subcommands.add_parser('stop', help=f'Stop {g.name}')
    
    #Restart Command
    parser_restart = subcommands.add_parser('restart', help=f'Restart {g.name}')
    parser_restart.add_argument('--logs', required=False, action="store_true", help=f'Show {g.name} logs', default=False)
    parser_restart.add_argument('--privileged', '-P', action='store_true', help='Use privileged mode for VMs')
    parser_restart.add_argument('--disk-limit', '-D', action='store_true', help='Limit disk size for VMs')
    parser_restart.add_argument('--expose-gameserver', '-E', action='store_true', help='Expose gameserver port')
    parser_restart.add_argument('--gameserver-port', default="127.0.0.1:8888", help='Gameserver port')

    #Clear Command
    parser_clear = subcommands.add_parser('clear', help='Clear data')
    parser_clear.add_argument('--all', '-A', action='store_true', help='Clear everything')
    parser_clear.add_argument('--config', '-c', action='store_true', help='Clear config file')
    parser_clear.add_argument('--prebuilded-container', '-P', action='store_true', help='Clear prebuilded container')
    parser_clear.add_argument('--prebuilder-image', '-B', action='store_true', help='Clear prebuilder image')
    parser_clear.add_argument('--prebuilt-image', '-I', action='store_true', help='Clear prebuilt image')
    parser_clear.add_argument('--wireguard', '-W', action='store_true', help='Clear wireguard data')
    parser_clear.add_argument('--checkers-data', '-C', action='store_true', help='Clear checkers data')
    parser_clear.add_argument('--gameserver-data', '-G', action='store_true', help='Clear gameserver data')

    #Status Command
    subcommands.add_parser('status', help='Show status')
    
    args = parser.parse_args(args=args_to_parse)

    return args

args = gen_args()

def composecmd(cmd, composefile=None):
    if composefile:
        cmd = f"-f {composefile} {cmd}"
    if not cmd_check("docker --version"):
        return puts("docker not found! please install docker!", color=colors.red)
    elif not cmd_check("docker ps"):
        return puts("Cannot use docker, the user hasn't the permission or docker isn't running", color=colors.red)
    elif cmd_check("docker compose --version"):
        return os.system(f"docker compose -p {g.compose_project_name} {cmd}")
    elif cmd_check("docker-compose --version"):
        return os.system(f"docker-compose -p {g.compose_project_name} {cmd}")
    else:
        return puts("docker compose not found! please install docker compose!", color=colors.red)

def check_already_running():
    return g.container_name in cmd_check(f'docker ps --filter "name=^{g.container_name}$"', get_output=True)

def prebuilder_exists():
    return g.prebuild_image in cmd_check(f'docker image ls --filter "reference={g.prebuild_image}"', get_output=True)

def prebuilt_exists():
    return g.prebuilt_image in cmd_check(f'docker image ls --filter "reference={g.prebuilt_image}"', get_output=True)

def remove_prebuilder():
    return cmd_check(f'docker image rm {g.prebuild_image}')

def remove_prebuilt():
    return cmd_check(f'docker image rm {g.prebuilt_image}')

def remove_prebuilded():
    return cmd_check(f'docker container rm {g.prebuilded_container}')

def remove_database_volume():
    return cmd_check('docker volume rm -f oasis_oasis-postgres-db')

def build_prebuilder():
    return cmd_check(f'docker build -t {g.prebuild_image} -f ./vm/Dockerfile.prebuilder ./vm/', print_output=True)

def build_prebuilt(privileged):
    return cmd_check(f'docker run -it {"--privileged" if privileged else "--runtime=sysbox-runc"} --name {g.prebuilded_container} {g.prebuild_image}', print_output=True)

def kill_builder():
    return cmd_check(f'docker kill {g.prebuilded_container}', no_stderr=True)

def commit_prebuilt():
    return cmd_check(f'docker commit {g.prebuilded_container} {g.prebuilt_image}:latest', print_output=True)


def write_compose(config: Union[Dict[str, Any], Config]):
    # Convert dict to Config object if needed
    if not isinstance(config, Config):
        config = Config.from_dict(config)
    
    with open(g.composefile,"wt") as compose:
        compose.write(dict_to_yaml({
            "services": {
                "router": {
                    "hostname": "router",
                    "dns": [config.dns],
                    "build": "./router",
                    "cap_add": [
                        "NET_ADMIN",
                        "SYS_MODULE",
                        "SYS_ADMIN",
                    ],
                    "sysctls": [
                        "net.ipv4.ip_forward=1",
                        "net.ipv4.tcp_timestamps=0",
                        "net.ipv4.conf.all.rp_filter=1",
                        "net.ipv6.conf.all.forwarding=0",
                    ],
                    "environment": {
                        "NTEAM": len(config.teams),
                        "RATE_NET": config.network_limit_bandwidth,
                    },
                    "volumes": [
                        "unixsk:/unixsk"
                    ],
                    "restart": "unless-stopped",
                    "networks": {
                        **{f"vm-team{team.id}": {
                            "priority": 10,
                            "ipv4_address": f"10.60.{team.id}.250"
                        } for team in config.teams},
                        "gameserver": {
                            "priority": 10,
                            "ipv4_address": "10.10.0.250"
                        },
                        "externalnet": {
                            "priority": 1,
                        },
                        **{
                            f"players{team.id}":{
                                "priority": 10,
                                "ipv4_address": f"10.80.{team.id}.250"
                            } for team in config.teams if not team.nop
                        }
                    }
                },
                "database": {
                    "hostname": "oasis-database",
                    "dns": [config.dns],
                    "image": "postgres:17",
                    "restart": "unless-stopped",
                    "environment": {
                        "POSTGRES_USER": "oasis",
                        "POSTGRES_PASSWORD": "oasis",
                        "POSTGRES_DB": "oasis"
                    },
                    "volumes": [
                        "oasis-postgres-db:/var/lib/postgresql/data"
                    ],
                    "networks": {
                        "internalnet": "",
                    }
                },
                "gameserver": {
                    "hostname": "gameserver",
                    "dns": [config.dns],
                    "build": "./game_server",
                    "restart": "unless-stopped",
                    "container_name": g.container_name,
                    "cap_add": [
                        "NET_ADMIN"
                    ],
                    **({
                        "ports": [
                            f"{config.gameserver_exposed_port}:80"
                        ]
                    } if config.gameserver_exposed_port is not None else {}),
                    "depends_on": [
                        "router",
                        "database",
                        *[f"team{team.id}" for team in config.teams]
                    ],
                    "networks": {
                        "internalnet": {
                            "priority": 1
                        },
                        "gameserver": {
                            "priority": 10,
                            "ipv4_address": "10.10.0.1"
                        }
                    },
                    "volumes": [
                        "./game_server/checkers:/app/checkers:z",
                        "unixsk:/unixsk",
                        f"./{g.config_file}:/app/{g.config_file}:z"
                    ]
                },
                **{
                    f"team{team.id}": {
                        "hostname": f"team{team.id}",
                        "dns": [config.dns],
                        "build": {
                            "context": "./vm",
                            "args": {
                                "TOKEN": team.token,
                            }
                        },
                        **({ "storage_opt": {"size":config.max_disk_size} } if config.max_disk_size else {}),
                        **({"privileged": "true"} if config.unsafe_privileged else { "runtime": "sysbox-runc" }),
                        "restart": "unless-stopped",
                        "networks": {
                            f"vm-team{team.id}": {
                                "ipv4_address": f"10.60.{team.id}.1"
                            }
                        },
                        "deploy":{
                            "resources":{
                                "limits":{
                                    "cpus": f'"{config.max_vm_cpus}"',
                                    "memory": config.max_vm_mem
                                }
                            }
                        }
                    } for team in config.teams
                },
                **{
                    f"wireguard{team.id}": {
                        "hostname": f"wireguard{team.id}",
                        "dns": [config.dns],
                        "build": "./wireguard",
                        "restart": "unless-stopped",
                        "cap_add": [
                            "NET_ADMIN",
                            "SYS_MODULE"
                        ],
                        "sysctls": [
                            "net.ipv4.ip_forward=1",
                            "net.ipv4.conf.all.src_valid_mark=1",
                        ],
                        "volumes": [
                            f"./wireguard/conf{team.id}:/config:z"
                        ],
                        "networks": {
                            f"players{team.id}": {
                                "ipv4_address": f"10.80.{team.id}.128"
                            }
                        },
                        "ports": [
                            f"{config.wireguard_start_port+team.id}:51820/udp"
                        ],
                        "environment": {
                            "PUID": os.getuid() if is_linux() else 0,
                            "PGID": os.getgid() if is_linux() else 0,
                            "TZ": "Etc/UTC",
                            "PEERS": config.wireguard_profiles,
                            "PEERDNS": config.dns,
                            "ALLOWEDIPS": "10.10.0.0/16, 10.60.0.0/16, 10.80.0.0/16",
                            "SERVERURL": config.server_addr,
                            "SERVERPORT": config.wireguard_start_port+team.id,
                            "INTERNAL_SUBNET": f"10.80.{team.id}.0/24",
                        }
                    } for team in config.teams if not team.nop
                }
            },
            "volumes": {
                "unixsk": "",
                "oasis-postgres-db": ""
            },
            "networks": {
                "externalnet": "",
                "internalnet": "",
                "gameserver": {
                    "internal": "true",
                    "driver": "macvlan",
                    "ipam": {
                        "driver": "default",
                        "config": [
                            {
                                "subnet": "10.10.0.0/24",
                                "gateway": "10.10.0.254",
                            }
                        ]
                    }
                },
                **{
                    f"vm-team{team.id}": {
                        "internal": "true",
                        "driver": "macvlan",
                        "ipam": {
                            "driver": "default",
                            "config": [
                                {
                                    "subnet": f"10.60.{team.id}.0/24",
                                    "gateway": f"10.60.{team.id}.254",
                                }
                            ]
                        }
                    } for team in config.teams
                },
                **{
                    f"players{team.id}": {
                        "driver": "bridge",
                        "ipam": {
                            "driver": "default",
                            "config": [
                                {
                                    "subnet": f"10.80.{team.id}.0/24",
                                    "gateway": f"10.80.{team.id}.254",
                                }
                            ]
                        }
                    } for team in config.teams if not team.nop
                }
            }
        }))

def try_to_remove(file):
    try:
        os.remove(file)
    except FileNotFoundError:
        pass

def clear_data(
    remove_config=True,
    remove_prebuilded_container=True,
    remove_prebuilder_image=True,
    remove_prebuilt_image=True,
    remove_wireguard=True,
    remove_checkers_data=True,
    remove_gameserver_data=True  
):
    if remove_gameserver_data:
        puts("Removing databse volume", color=colors.yellow)
        remove_database_volume()
    if remove_wireguard:
        puts("Removing wireguard configs", color=colors.yellow)
        for file in os.listdir("./wireguard"):
            if file.startswith("conf"):
                shutil.rmtree(f"./wireguard/{file}", ignore_errors=True)
    if remove_config:
        puts("Removing config.json", color=colors.yellow)
        try_to_remove(g.config_file)
    if remove_prebuilded_container:
        puts("Removing prebuilded image", color=colors.yellow)
        remove_prebuilded()
    if remove_prebuilder_image:
        puts("Removing prebuilder image", color=colors.yellow)
        remove_prebuilder()
    if remove_prebuilt_image:
        puts("Removing prebuilt image", color=colors.yellow)
        remove_prebuilt()
    if remove_checkers_data:
        puts("Removing checkers data", color=colors.yellow)
        for service in os.listdir("./game_server/checkers"):
            shutil.rmtree(f"./game_server/checkers/{service}/flag_ids", ignore_errors=True)

def clear_data_only(
    remove_config=False,
    remove_prebuilded_container=False,
    remove_prebuilder_image=False,
    remove_prebuilt_image=False,
    remove_wireguard=False,
    remove_checkers_data=False,
    remove_gameserver_data=False
):
    clear_data(
        remove_config=remove_config,
        remove_prebuilded_container=remove_prebuilded_container,
        remove_prebuilder_image=remove_prebuilder_image,
        remove_prebuilt_image=remove_prebuilt_image,
        remove_wireguard=remove_wireguard,
        remove_checkers_data=remove_checkers_data,
        remove_gameserver_data=remove_gameserver_data
    )

def try_mkdir(path):
    try:
        os.mkdir(path)
    except FileExistsError:
        pass

def generate_teams_array(number_of_teams: int, enable_nop_team: bool, wireguard_start_port: int) -> List[Team]:
    teams = []
    for i in range(number_of_teams + (1 if enable_nop_team else 0)):
        team = Team(
            id=i,
            name=f'Team {i}',
            token=secrets.token_hex(32),
            wireguard_port=wireguard_start_port+i,
            nop=(i == 0 and enable_nop_team),
            image=""
        )
        if i == 0 and enable_nop_team:
            team.name = 'Nop Team'
        teams.append(team)
    return teams

def get_input(prompt: str, default = None, is_required: bool = False, default_prompt: str = None):
    if is_required:
        prompt += " (REQUIRED, no default): "
    elif default_prompt:
        prompt += f" (default={default_prompt}): "
    else:
        prompt += f" (default={default}): "
    value = input(prompt).strip()
    if value != "":
        return value
    if is_required:
        while value == "":
            value = input(prompt).strip()
        return value
    return default

def config_input() -> Config:
    # abs() put for consistency with the other options
    default_number_of_teams = args.number_of_teams
    args.number_of_teams = abs(int(get_input('Number of teams, >= 0 and < 250', default_number_of_teams)))
    while args.number_of_teams < 0 or args.number_of_teams >= 250:
        args.number_of_teams = abs(int(get_input('Number of teams, >= 0 and < 250', default_number_of_teams)))

    # abs() put for consistency with the other options
    default_wireguard_start_port = args.wireguard_start_port
    args.wireguard_start_port = abs(int(get_input(f'Wireguard start port, >= 1 and <= {65535-args.number_of_teams}', default_wireguard_start_port)))
    while args.wireguard_start_port < 1 or args.wireguard_start_port > 65535-args.number_of_teams:
        args.wireguard_start_port = abs(int(get_input(f'Wireguard start port, >= 1 and <= {65535-args.number_of_teams}', default_wireguard_start_port)))

    args.wireguard_profiles      = abs(int(get_input('Number of wireguard profiles for each team', args.wireguard_profiles)))
    args.server_addr             = get_input('Server address', is_required=True)
    args.dns                     = get_input('DNS', args.dns)

    args.start_time              = get_input('Start time, in RFC 3339 (YYYY-mm-dd HH:MM:SS+/-zz:zz)')
    args.end_time                = get_input('End time, in RFC 3339 (YYYY-mm-dd HH:MM:SS+/-zz:zz)')
    args.tick_time               = abs(int(get_input('Tick time in seconds', args.tick_time)))
    args.flag_expire_ticks       = abs(int(get_input('Number of ticks after which each flag expires', args.flag_expire_ticks)))

    args.initial_service_score   = abs(int(get_input('Initial service score', args.initial_service_score)))
    args.max_flags_per_request   = abs(int(get_input('Max flags per request', args.max_flags_per_request)))
    args.submission_timeout      = abs(float(get_input('Submission timeout', args.submission_timeout)))
    args.network_limit_bandwidth = get_input('Network limit bandwidth', args.network_limit_bandwidth)

    args.max_vm_cpus             = get_input('Max VM CPUs', args.max_vm_cpus)
    args.max_vm_mem              = get_input('Max VM Memory', args.max_vm_mem)
    if get_input('Enable disk limit? (REQUIRES XFS FILESYSTEM)', 'yes').lower().startswith('y'):
        args.max_disk_size       = get_input('Max VM disk size', args.max_disk_size)
    else:
        args.max_disk_size = None
    args.expose_gameserver = get_input('Expose externally the gameserver scoreboard?', 'yes').lower().startswith('y')
    if args.expose_gameserver:
        args.gameserver_port = get_input('Insert with witch port or ip:port to expose the gameserver scoreboard', args.gameserver_port)
    
    args.gameserver_token        = get_input('Gameserver token', default_prompt='randomly generated')
    args.enable_nop_team         = get_input('Enable NOP team?', 'yes').lower().startswith('y')
    args.privileged              = not get_input('Use sysbox to run the VMs? (to prevent docker escape)', 'yes').lower().startswith('y')
    if args.privileged:
        puts("Privileged mode enabled (DO NOT USE THIS IN PRODUCTION)", color=colors.yellow)

    gameserver_exposed_port = None
    if args.expose_gameserver:
        gameserver_exposed_port = args.gameserver_port

    # Create teams
    teams = generate_teams_array(args.number_of_teams, args.enable_nop_team, args.wireguard_start_port)
    
    # Create and return the Config object
    return Config(
        wireguard_start_port=args.wireguard_start_port,
        wireguard_profiles=args.wireguard_profiles,
        server_addr=args.server_addr,
        dns=args.dns,
        start_time=datetime.fromisoformat(args.start_time).isoformat() if args.start_time else None,
        end_time=datetime.fromisoformat(args.end_time).isoformat() if args.end_time else None,
        tick_time=args.tick_time,
        flag_expire_ticks=args.flag_expire_ticks,
        initial_service_score=args.initial_service_score,
        max_flags_per_request=args.max_flags_per_request,
        submission_timeout=args.submission_timeout,
        network_limit_bandwidth=args.network_limit_bandwidth,
        max_vm_cpus=args.max_vm_cpus,
        max_vm_mem=args.max_vm_mem,
        max_disk_size=args.max_disk_size,
        gameserver_token=args.gameserver_token if args.gameserver_token else secrets.token_hex(32),
        unsafe_privileged=args.privileged,
        gameserver_exposed_port=gameserver_exposed_port,
        debug=False,
        teams=teams
    )

def create_config(data: Union[Dict[str, Any], Config]) -> Config:
    if not isinstance(data, Config):
        data = Config.from_dict(data)
    data.save_to_file(g.config_file)
    return data

def config_exists():
    return os.path.isfile(g.config_file)

def read_config() -> Config:
    return Config.from_json_file(g.config_file)

def main():
    if args.command == "start":
        if args.config_only:
            if config_exists():
                puts(f"Config file already exists! please edit {g.config_file}", color=colors.red)
                return
            config = config_input()
            create_config(config)
            puts(f"Config file generated!, you can customize it by editing {g.config_file}", color=colors.green)
            return

    if not cmd_check("docker --version"):
        puts("docker not found! please install docker!", color=colors.red)
    if not cmd_check("docker ps"):
        puts("docker is not running, please install docker and docker compose!", color=colors.red)
        exit()
    elif not cmd_check("docker-compose --version") and not cmd_check("docker compose --version"):
        puts("docker compose not found! please install docker compose!", color=colors.red)
        exit()
    
    if args.command:
        match args.command:
            case "start":
                if check_already_running():
                    puts(f"{g.name} is already running!", color=colors.yellow)
                if not config_exists():
                    config = config_input()
                    create_config(config)
                else:
                    config = read_config()
                if args.config_only:
                    puts(f"Config file generated!, you can customize it by editing {g.config_file}", color=colors.green)
                    return
                if len(config.teams) > 0:
                    if not prebuilt_exists():
                        puts("Prebuilt image not found!", color=colors.yellow)
                        puts("Clearing old setup images...", color=colors.yellow)
                        remove_prebuilded()
                        remove_prebuilt()
                        puts("Building the prebuilder image", color=colors.yellow)
                        if not build_prebuilder():
                            puts("Error building prebuilder image", color=colors.red)
                            exit(1)
                        puts("Executing prebuilder to create VMs' base image", color=colors.yellow)
                        if not build_prebuilt(config.unsafe_privileged):
                            puts("Error building prebuilt image", color=colors.red)
                            exit(1)
                        puts("Saving base VM container as image to be used to build the CTF services\n(this action can take a while and produces no output)", color=colors.yellow)
                        if not commit_prebuilt():
                            puts("Error commiting prebuilt image", color=colors.red)
                            exit(1)
                        puts("Clear unused images", color=colors.yellow)
                        remove_prebuilded()
                
                if not config_exists():
                    puts(f"Config file not found! please run {sys.argv[0]} start", color=colors.red)
                
                else:
                    puts(f"{g.name} is starting!", color=colors.yellow)
                    write_compose(read_config())
                    puts(f"Running 'docker compose up -d{' --build' if use_build_on_compose else ''}'\n", color=colors.green)
                    composecmd(f"up -d{' --build' if use_build_on_compose else ''} --remove-orphans", g.composefile)
            case "compose":
                if not config_exists():
                    puts(f"Config file not found! please run {sys.argv[0]} start", color=colors.red)
                else:
                    write_compose(read_config())
                    compose_cmd = " ".join(args.compose_args)
                    puts(f"Running 'docker compose {compose_cmd}'\n", color=colors.green)
                    composecmd(compose_cmd, g.composefile)
            case "restart":
                if not config_exists():
                    puts(f"Config file not found! please run {sys.argv[0]} start", color=colors.red)
                elif check_already_running():
                    write_compose(read_config())
                    puts("Running 'docker compose restart'\n", color=colors.green)
                    composecmd("restart", g.composefile)
                else:
                    puts(f"{g.name} is not running!" , color=colors.red, is_bold=True, flush=True)
            case "stop":
                if not config_exists():
                    puts(f"Config file not found! please run {sys.argv[0]} start", color=colors.red)
                elif check_already_running():
                    write_compose(read_config())
                    puts("Running 'docker compose down'\n", color=colors.green)
                    composecmd("down --remove-orphans", g.composefile)
                else:
                    puts(f"{g.name} is not running!" , color=colors.red, is_bold=True, flush=True)
            case "clear":
                if check_already_running():
                    puts(f"{g.name} is running! please stop it before clearing the data", color=colors.red)
                    exit(1)
                if True not in vars(args).values():
                    clear_data(remove_config=False)
                if args.all:
                    puts("This will clear everything, EVEN THE CONFIG JSON, are you sure? (y/N): ", end="")
                    if input().lower() != 'y':
                        return
                    puts("Clearing everything (even config!!)", color=colors.yellow)
                    clear_data()
                if args.config:
                    clear_data_only(remove_config=True)
                if args.prebuilded_container:
                    clear_data_only(remove_prebuilded_container=True)
                if args.prebuilder_image:
                    clear_data_only(remove_prebuilder_image=True)
                if args.prebuilt_image:
                    clear_data_only(remove_prebuilt_image=True)
                if args.wireguard:
                    clear_data_only(remove_wireguard=True)
                if args.checkers_data:
                    clear_data_only(remove_checkers_data=True)
                if args.gameserver_data:
                    clear_data_only(remove_gameserver_data=True)
                puts("Whatever you specified has been cleared!", color=colors.green, is_bold=True)
            case "status":
                if check_already_running():
                    puts(f"{g.name} is running!", color=colors.green)

    
    if "logs" in args and args.logs:
        if config_exists():
            write_compose(read_config())
        else:
            puts(f"Config file not found! please run {sys.argv[0]} start", color=colors.red)
        composecmd("logs -f")


if __name__ == "__main__":
    try:
        try:
            main()
        finally:
            kill_builder()
            if os.path.isfile(g.composefile) and not g.keep_file:
                os.remove(g.composefile)
    except KeyboardInterrupt:
        print()


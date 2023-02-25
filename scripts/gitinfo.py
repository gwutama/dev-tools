#!/usr/bin/env python3

import argparse
import os
from common.console import Term
import git
import pathlib
import json
import time
import sys
import json

try:
    from versioning import version_string
except ModuleNotFoundError:
    def version_string():
        return "Unknown version"


def find_root_git_dir(dir):
    # can we find .git directory here? if not got up until we find one.
    git_dir = None
    tmp_dir = dir
    
    # Repository dir always has .git directory. Submodule dir always has .git file.
    # Check whether this is a submodule directory first.
    if not os.path.isfile(str(tmp_dir) + "/.git"):    
        # Ok not, a submodule directory, find git root repository dir.
        while not os.path.isdir(str(tmp_dir) + "/.git"):
            path = pathlib.Path(dir)
            
            # cant go up directory anymore, exit loop
            if path.parent == tmp_dir:
                break

            tmp_dir = path.parent            
    
    # .git can be a dir or a path
    path = str(tmp_dir) + "/.git"
    
    if os.path.isdir(path) or os.path.isfile(path):
        git_dir = str(tmp_dir)
    else:
        Term.fail("Root git directory not found.")
        sys.exit(1)        
        
    return git_dir


def remote_url(dir):
    g = git.cmd.Git(dir)
    out = g.execute(["git", "config", "--get", "remote.origin.url"])
    return out


def general_info(dir):
    git_dir = find_root_git_dir(dir)

    if git_dir is None:
        return {}

    repo = git.Repo(git_dir)
    return {
        "absolute_path": git_dir,
        "relative_path": os.getcwd().replace(git_dir, "."),
        "branch": repo.active_branch.name,
        "remote_url": remote_url(git_dir)
    }


def print_general_info(dir):
    info = general_info(dir)
    
    if not info:
        Term.fail("Failed querying general info.")        
        sys.exit(4)
        
    Term.ok("GENERAL", True)
    Term.info("  Branch:\t" + info["branch"])        
    Term.info("  Remote URL:\t" + info["remote_url"])            
    Term.info("  Abs path:\t" + info["absolute_path"])
    Term.info("  Rel path:\t" + info["relative_path"])    
    

def submodules_info(dir):
    git_dir = find_root_git_dir(dir)

    if git_dir is None:
        return []

    repo = git.Repo(git_dir)
    sms = repo.submodules
    out = []
    
    for sm in sms:
        item = {
            "name": sm.name,
            "absolute_path": git_dir + "/" + sm.path,
            "relative_path": sm.path,
            "remote_url": sm.url,
            "last_commit": last_commit_info(git_dir + "/" + sm.path)
        }
        out.append(item)
        
    return out


def print_submodules_info(dir):
    info = submodules_info(dir)
    
    Term.ok("SUBMODULES", True)
    
    if len(info):        
        for item in info:
            Term.info("  Name:\t\t" + item["name"])                
            Term.info("  Remote URL:\t" + item["remote_url"])                
            Term.info("  Abs path:\t" + item["absolute_path"])        
            Term.info("  SHA:\t\t" + item["last_commit"]["sha"][:7])                
            print()        
    else:
        Term.info("  None detected.")
            

def last_commit_info(dir):
    git_dir = find_root_git_dir(dir)

    if git_dir is None:
        return {}

    repo = git.Repo(git_dir)    
    commit = repo.head.commit
    return {
        "sha": commit.hexsha,
        "author": commit.author.name,
        "email": commit.author.email,
        "date": commit.committed_datetime.strftime("%Y-%m-%d"),
        "time": commit.committed_datetime.strftime("%H:%M:%S"),        
    }    


def print_last_commit_info(dir):
    info = last_commit_info(dir)
    
    if not info:
        Term.fail("Failed querying commit info.")        
        sys.exit(4)
    
    Term.ok("LAST COMMIT", True)
    Term.info("  SHA:\t\t" + info["sha"][:7])        
    Term.info("  Author:\t" + "%s <%s>" % (info["author"], info["email"]))
    Term.info("  Date:\t\t" + info["date"] + " " + info["time"])
    
    
def version_info(dir):
    general = general_info(dir)   
    
    # Default dict
    version = {
        "repository": "",
        "major": "0",
        "minor": "0",
        "patch": "0",
        "revision": ""       
    }    
    
    if not general:
        return version

    # Repo name
    repo = os.path.basename(general["remote_url"]) # only the repo name + .git
    repo = os.path.splitext(repo)[0] # only repo name
    version["repository"] = repo

    # revision info
    commit = last_commit_info(dir)
    
    if not commit:
        return version
    
    version["revision"] = commit["sha"]
   
    # major, minor patch info     
    # Tag is always in format: [major].[minor].[patch]
    tag = general["branch"]
    splitted = tag.split(".")
    
    if len(splitted) != 3:
        return version
    
    version["major"] = splitted[0]
    version["minor"] = splitted[1]
    version["patch"] = splitted[2]
        
    return version
    
    
def print_version_info(dir):
    info = version_info(dir)  

    Term.ok("VERSION", True)  
   
    if not info:
        Term.info("  Current branch is not a tag.")
        return      

    Term.info("  Repository:\t" + info["repository"])                
    Term.info("  Major:\t" + info["major"])            
    Term.info("  Minor:\t" + info["minor"])                
    Term.info("  Patch:\t" + info["patch"])                
    Term.info("  Revision:\t" + info["revision"][:7])


def print_key_value(dir, key=None):
    # If requested key is unset, then show list of keys
    # otherwise show its value, if available.
    key_map  = {
        "general.branch": "",
        "general.remote_url": "",        
        "general.absolute_path": "",        
        "general.relative_path": "",        
        "commit.sha": "",
        "commit.author": "",
        "commit.email": "",        
        "commit.date": "",
        "commit.time": "",
        "version.repository": "",
        "version.major": "",        
        "version.minor": "",       
        "version.patch": "",        
        "version.revision": ""        
    }
    
    # If requested key is unset, then show list of keys
    # otherwise show its value, if available.
    if key is None:
        for key in key_map:
            Term.info(key)
        return    

    # key is set, read all values first and query value of the requested key
    general = general_info(dir)
    
    if not general:
        Term.fail("Failed querying general info.")
        sys.exit(4)
    
    commit = last_commit_info(dir)
    
    if not commit:
        Term.fail("Failed querying commit info.")
        sys.exit(4)
    
    version = version_info(dir)

    key_map["general.branch"] = general["branch"]
    key_map["general.remote_url"] = general["remote_url"]        
    key_map["general.absolute_path"] = general["absolute_path"]        
    key_map["general.relative_path"] = general["relative_path"]
    key_map["commit.sha"] = commit["sha"]
    key_map["commit.author"] = commit["author"]
    key_map["commit.email"] = commit["email"]        
    key_map["commit.date"] = commit["date"]
    key_map["commit.time"] = commit["time"]
    key_map["version.repository"] = version["repository"]
    key_map["version.major"] = version["major"]        
    key_map["version.minor"] = version["minor"]        
    key_map["version.patch"] = version["patch"]
    key_map["version.revision"] = version["revision"]        

    # If requested key is unset, then show list of keys
    # otherwise show its value, if available.
    if key in key_map:
        Term.info(key_map[key])
    else:
        Term.fail("Invalid key: " + key)
        sys.exit(3)


def generate_python_version_file(dir, out_dir):
    general = general_info(dir)
    file_path = out_dir + "/versioning.py"
    
    if not general:
        Term.fail("Failed querying general info.")
        sys.exit(4)
    
    version = version_info(dir)

    out = """
VERSION_MAJOR = "%s"
VERSION_MINOR = "%s"
VERSION_PATCH = "%s"
VERSION_REVISION = "%s"
VERSION_REPOSITORY = "%s"
VERSION_BRANCH = "%s"

def version_string():
    return "%%s.%%s.%%s-%%s (%%s)" %% (VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH, VERSION_REVISION[:7], VERSION_REPOSITORY)

""" % (version["major"], version["minor"], version["patch"], version["revision"],
       version["repository"], general["branch"])
    
    try:
        with open(file_path, 'w') as f:
            f.truncate()
            f.write(out)
            
        Term.ok("Generated file: " + file_path)
    except IOError:
        Term.fail("Error writing file: " + file_path)
        

def generate_json_version_file(dir, out_dir):
    general = general_info(dir)
    file_path = out_dir + "/versioning.json"
    
    if not general:
        Term.fail("Failed querying general info.")
        sys.exit(4)
    
    version = version_info(dir)

    out = {
        "VERSION_MAJOR": version["major"],
        "VERSION_MINOR": version["minor"],
        "VERSION_PATCH": version["patch"],
        "VERSION_REVISION": version["revision"],
        "VERSION_REPOSITORY": version["repository"],
        "VERSION_BRANCH": general["branch"]
    }
    
    try:
        with open(file_path, 'w') as f:
            f.truncate()
            json.dump(out, f, indent=4)
            
        Term.ok("Generated file: " + file_path)
    except IOError:
        Term.fail("Error writing file: " + file_path)


if __name__ == "__main__":
    desc = "Utility for retrieving information of a git local directory."
    copy = "Copyright 2019 Galuh Utama <galuh.utama@gwutama.de>"
    parser = argparse.ArgumentParser(description=desc, epilog=copy)
    parser.add_argument("-v", "--version", action="store_true", help="Show version.")                      
    parser.add_argument("-d", "--dir", action="store",
                        help="Local git directory. Defaults to current directory.",
                        default=os.getcwd())
    parser.add_argument("--get", action="store",
                        help="Get a value of git info and print to stdout, useful for CI. "
                        "Pass --list-keys to display all available keys.",
                        default=None)      
    parser.add_argument("--list-keys", action="store_true", help="List all available keys for --get.")                                          
    parser.add_argument("--gen-python", action="store_true", help="Generate python versioning script file. "
                        "The file will be generated in current working directory.")                        
    parser.add_argument("--gen-json", action="store_true", help="Generate json versioning file. "
                        "The file will be generated in current working directory.")                                                
    args = parser.parse_args()
    
    try:
        if not args.list_keys and not args.get and not args.gen_python and not args.version and not args.gen_json:
            print_general_info(args.dir)
            print()
            print_last_commit_info(args.dir)        
            print()        
            print_submodules_info(args.dir)
            print()        
            print_version_info(args.dir)        
            sys.exit(0)
        elif args.list_keys and not args.get and not args.gen_python and not args.version and not args.gen_json:
            print_key_value(args.dir)
            sys.exit(0)            
        elif args.get and not args.list_keys and not args.gen_python and not args.version and not args.gen_json:
            print_key_value(args.dir, args.get)
            sys.exit(0)            
        elif args.gen_python and not args.list_keys and not args.get and not args.version and not args.gen_json:
            generate_python_version_file(args.dir, os.getcwd())
            sys.exit(0)            
        elif args.version and not args.gen_python and not args.list_keys and not args.get and not args.gen_json:
            print(version_string())
            sys.exit(0)            
        elif args.gen_json and not args.version and not args.gen_python and not args.list_keys and not args.get:
            generate_json_version_file(args.dir, os.getcwd())
            sys.exit(0)
        else:
            Term.fail("Invalid parameters")
            sys.exit(2)
    except KeyboardInterrupt:
        print("Keyboard interrupt requested")
        sys.exit(0)

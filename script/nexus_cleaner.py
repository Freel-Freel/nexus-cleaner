#!/bin/env python
# Idea: https://technology.amis.nl/author/maarten-smeets/
# Editor: Alexander Grigoriev

import xml.etree.ElementTree as ET
import httplib
import datetime
import time
import string
import base64
import getpass
import os
import sys
import re
import getopt
import random
import requests
from monitoring import prometheus

now_datetime = datetime.datetime.now()

mon = prometheus()
mon.monitoring_port = "19091"
mon.job_name = "nexus_cleaner"
NEXUSHOST = ""
NEXUSPORT = "443"
NEXUSREPOSITORY = ""
NEXUSBASEURL = "/nexus/service/local/repositories/"
if 'NEXUS_PASSWORD' in os.environ:
  NEXUSUSERNAME = os.environ['NEXUS_USERNAME']
  NEXUSPASSWORD = os.environ['NEXUS_PASSWORD']
  PUSHGATEWAY_INSTANCE = "localhost:19091"
  mon.monitoring_host = "localhost"
else:
  NEXUSUSERNAME = raw_input('Username: ')
  NEXUSPASSWORD = getpass.getpass('Password: ')
  PUSHGATEWAY_INSTANCE = "127.0.0.1:19091"
  mon.monitoring_host = "127.0.0.1"
GLOBAL_TOTAL_ERRORS = 0
GLOBAL_TOTAL_REMOVED = 0
GLOBAL_TOTAL_ARTIFACTS_COUNT = 0
GLOBAL_ERRORS_LIST = []
GLOBAL_DELETE_ERRORS = False

artifacts_rules = {
  'DEFAULT': {'<DEFAULT>': {'keep_versions': 0, 'keep_minors': 0, 'keep_old_days': 183}},
  'RiskPlatform_snapshot': {
    '<DEFAULT>': {'keep_versions': 0, 'keep_minors': 0, 'keep_old_days': 30},
    '/configs/configs': {'keep_versions': 0, 'keep_minors': 0, 'keep_old_days': 92},
    '/jdeployment/': {'keep_versions': 15, 'keep_minors': 0, 'keep_old_days': 0},
    '/ru/sbt/risk/deployment/': {'keep_versions': 10, 'keep_minors': 0, 'keep_old_days': 0}
  },
  'Release': {
    '<DEFAULT>': {'keep_versions': 0, 'keep_minors': 0, 'keep_old_days': 365}
  }
}

def usage():
  script_name = sys.argv[0]
  print(script_name+""" [-h] -{[lcde]} -r <repository> --nexus_host=<hostname> --nexus_port=<port> --nexus_base_url=<url>
  -h                This message
  -c                Check artifacts from local file against artifacts_rules
  -l                Create nexus_artifacts_list.txt
  -r                Nexus repository, e.g. Release
  -d                Delete data from nexus repository
  -e                Delete errors (bad versions) from nexus repository
  --nexus_host      Default:  ONLY DEFAULT
  --nexus_port      Default: 443 ONLY DEFAULT
  --nexus_base_url  Default: /nexus/service/local/repositories/ ONLY DEFAULT
""")

def main(argv):
  global GLOBAL_DELETE_ERRORS
  recreate_keep_list()
  recreate_doomed_list()
  total_doomed = 0
  total_kept = 0
  artifacts_list = []
  DELETEFROMNEXUS = False
  delete_errors = GLOBAL_DELETE_ERRORS
  format_args = {"instance": mon.GetInstance(), "repo": ""}
  try:
    opts, args = getopt.getopt(argv,"edhclr:",["nexus_host=", "nexus_port=", "nexus_base_url="])
  except getopt.GetoptError:
    usage()
    sys.exit(2)

  for opt, arg in opts:
    if opt in ("-h"):
      usage()
      sys.exit(1)
    elif opt in ("-l"):
      mon.push("nexus_cleaner_artifacts_list", 0, {"job": "nexus_cleaner", "list": "all", "repo": format_args["repo"]})
      mon.push("nexus_cleaner_artifacts_list", 0, {"job": "nexus_cleaner", "list": "errors", "repo": format_args["repo"]})
      recreate_whole_list()
      artifacts_list = get_artifact_names_rec(NEXUSREPOSITORY, "/", delete_errors)
      print("GLOBAL COUNTER: {} len: {}".format(GLOBAL_TOTAL_ARTIFACTS_COUNT, len(artifacts_list)))
# summary, just for compare with cumulative counter GLOBAL_TOTAL_ARTIFACTS_COUNT
      mon.push("nexus_cleaner_artifacts_list", len(artifacts_list), {"job": "nexus_cleaner", "list": "all", "repo": format_args["repo"]})
    elif opt in ("-c"):
      artifacts_list = []
      mon.push("nexus_cleaner_versions_list", 0, {"job": "nexus_cleaner", "list": "doomed", "repo": format_args["repo"]})
      mon.push("nexus_cleaner_versions_list", 0, {"job": "nexus_cleaner", "list": "kept", "repo": format_args["repo"]})
      artifacts_list_file = "nexus_artifacts_list.txt"
      f = open(artifacts_list_file, 'r')
      for line in f:
        artifacts_list.append(line.rstrip())
      f.close
      mon.push("nexus_cleaner_artifacts_list", len(artifacts_list), {"job": "nexus_cleaner", "list": "all", "repo": format_args["repo"]})
    elif opt in ("-r"):
      NEXUSREPOSITORY = arg
      format_args["repo"] = NEXUSREPOSITORY
    elif opt in ("-d"):
      DELETEFROMNEXUS = True
    elif opt in ("-e"):
      GLOBAL_DELETE_ERRORS = True
      delete_errors = True

  if hasattr(format_args, "repo"):
    usage()
    sys.exit(1)

  for artifact in artifacts_list:
    if artifact[-1] == '/':
      artifact = artifact[0:len(artifact)-1]
    splited = artifact.rsplit('/', 1)
    if len(splited) != 2:
      print("Failed to split {} by slashes!".format(artifact))
      continue
    ARTIFACTGROUP = splited[0]
    ARTIFACTNAME = splited[1]
    KEEP_RECENT_MINORS, KEEP_VERSIONS, ARTIFACTMAXLASTMODIFIED = get_special_rules(NEXUSREPOSITORY, artifact, artifacts_rules)
    print("group: {} artifact: {} versions: {} minors: {} not earlier than {}"
      .format(ARTIFACTGROUP, ARTIFACTNAME, KEEP_VERSIONS, KEEP_RECENT_MINORS, ARTIFACTMAXLASTMODIFIED))
    items = []
    keep_versions = []
    to_delete = []
    if ARTIFACTNAME: # TODO move this check closer to variable setting
      artifact_versions = get_version_listing_xml(NEXUSREPOSITORY, ARTIFACTGROUP, ARTIFACTNAME)
      if artifact_versions is None:
        print("Nexus not responds? Waiting few seconds...")
        time.sleep(random.randint(0,5))
        continue
    else:
      print("ARTIFACTNAME is required! Exiting...")
      sys.exit()
    content_items = artifact_versions.findall('./data/content-item')
    items = parse_xml_content(ARTIFACTNAME, content_items)
    keep_versions = get_keep_versions(items, KEEP_RECENT_MINORS, KEEP_VERSIONS)
    ### print("Keep versions:")
    ### print(keep_versions)
    items.sort(cmp=compare_versions)
    for item in items:
      print("{} {}".format(item['name'], item['version'])),
      if is_doomed(item, keep_versions, ARTIFACTMAXLASTMODIFIED):
        print("-")
        to_delete.append(item)
        save_artifact_to_doomed_list(item['name'], item['version'], ARTIFACTGROUP);
        total_doomed += 1
        mon.push("nexus_cleaner_versions_list", total_doomed, {"job": "nexus_cleaner", "list": "doomed", "repo": format_args["repo"]})
      else:
        print("+")
        save_artifact_to_keep_list(item['name'], item['version'], ARTIFACTGROUP);
        total_kept += 1
        mon.push("nexus_cleaner_versions_list", total_kept, {"job": "nexus_cleaner", "list": "kept", "repo": format_args["repo"]})
    if DELETEFROMNEXUS:
      delete_from_nexus(NEXUSREPOSITORY, to_delete)
  print("Total: doomed: {}, kept: {}, errors: {}, errors_removed: {} . Reseting 'removed'.".format(total_doomed, total_kept, GLOBAL_TOTAL_ERRORS, len(GLOBAL_ERRORS_LIST)))
  mon.push("nexus_cleaner_versions_list", 0, {"job": "nexus_cleaner", "list": "removed", "repo": format_args["repo"]})

def get_special_rules(NEXUSREPOSITORY, artifact, artifacts_rules):
  keep_recent_minors = None
  keep_versions = None
  artifactmaxlastmodified = None
  try:
    rules = artifacts_rules[NEXUSREPOSITORY]
  except:
    rules = artifacts_rules['DEFAULT']
  for i, (art, rule) in enumerate(artifacts_rules.items()):
    if art in artifact:
      keep_recent_minors = rule['keep_minors']
      keep_versions = rule['keep_versions']
      artifactmaxlastmodified = now_datetime - datetime.timedelta(days=rule['keep_old_days'])
  if not keep_recent_minors:
      keep_recent_minors = rules['<DEFAULT>']['keep_minors']
      if not keep_recent_minors:
        keep_recent_minors = artifacts_rules['DEFAULT']['<DEFAULT>']['keep_minors']
  if not keep_versions:
      keep_versions = rules['<DEFAULT>']['keep_versions']
      if not keep_versions:
        keep_versions = artifacts_rules['DEFAULT']['<DEFAULT>']['keep_versions']
  if not artifactmaxlastmodified:
      artifactmaxlastmodified = now_datetime - datetime.timedelta(days=rules['<DEFAULT>']['keep_old_days'])
      if not artifactmaxlastmodified:
        artifactmaxlastmodified = now_datetime - datetime.timedelta(days=artifacts_rules['DEFAULT']['<DEFAULT>']['keep_old_days'])
  return [keep_recent_minors, keep_versions, artifactmaxlastmodified]

def get_artifact_names_rec(nexus_repository, path='/', delete_errors = False):
    global GLOBAL_TOTAL_ERRORS, PUSHGATEWAY_INSTANCE, GLOBAL_TOTAL_ARTIFACTS_COUNT, GLOBAL_ERRORS_LIST, mon
    global NEXUSUSERNAME, NEXUSPASSWORD
    format_args = {"instance": PUSHGATEWAY_INSTANCE, "repo": nexus_repository}
    artifacts = []
    if path[-1] != '/':
      path += '/'
    time.sleep(random.randint(0,2))
    conn = httplib.HTTPSConnection(NEXUSHOST, NEXUSPORT)
    url = NEXUSBASEURL + nexus_repository + "/content" + path.replace(".", "/")
    auth = string.strip(base64.encodestring(NEXUSUSERNAME + ':' + NEXUSPASSWORD))
    conn.putrequest("GET", url)
    conn.putheader("Authorization", "Basic %s" % auth)
    conn.endheaders()
    conn.send("")

    response = conn.getresponse()
    if (response.status == 200):
      artifacts.append(path)
      elem = ET.fromstring(response.read())
      items = elem.findall('./data/content-item')
      for item in items:
        leaf = item.find("./leaf").text
        text = item.find("./text").text
        relative_path = item.find("./relativePath").text
        if leaf == "false":
          if not is_version(text):
            fout = open('nexus_artifacts_list.txt', 'a')
            fout.write("{}\n".format(relative_path))
            fout.close()
            GLOBAL_TOTAL_ARTIFACTS_COUNT += 1
            children = get_artifact_names_rec(nexus_repository, relative_path, delete_errors)
            if children:
              artifacts.extend(children)
      print("{} ({}), total: {}".format(path, len(artifacts), GLOBAL_TOTAL_ARTIFACTS_COUNT))
      mon.push("nexus_cleaner_artifacts_list", GLOBAL_TOTAL_ARTIFACTS_COUNT, {"job": "nexus_cleaner", "list": "all", "repo": format_args["repo"]})
      return artifacts
    else:
      fout = open('nexus_errors_list.txt', 'a')
      fout.write("{} - response: {}... ".format(path, response.status))
      if response.status == 404:
        GLOBAL_ERRORS_LIST.append(path)
        if GLOBAL_DELETE_ERRORS and delete_errors:
          r = delete_from_nexus(nexus_repository, [{"path": path}])
          if not r:
            fout.write("removed".format(path, response.status))
      fout.write("\n".format(path, response.status))
      fout.close()
      GLOBAL_TOTAL_ERRORS += 1
      mon.push("nexus_cleaner_artifacts_list", GLOBAL_TOTAL_ERRORS, {"job": "nexus_cleaner", "list": "errors", "repo": format_args["repo"]})
      return []

def delete_from_nexus(nexus_repository, items):
  global NEXUSHOST, NEXUSPORT, GLOBAL_TOTAL_REMOVED, PUSHGATEWAY_INSTANCE, mon, GLOBAL_DELETE_ERRORS
  if not GLOBAL_DELETE_ERRORS:
    return 3
  format_args = {"instance": PUSHGATEWAY_INSTANCE, "repo": nexus_repository}
  for item in items:
    url = NEXUSBASEURL + nexus_repository + "/content" + item['path']
    print("Sending HTTP DELETE request to https://{}:{}{}".format(NEXUSHOST, NEXUSPORT, url))
    auth = string.strip(base64.encodestring(NEXUSUSERNAME + ':' + NEXUSPASSWORD))
    service = httplib.HTTPS(NEXUSHOST,NEXUSPORT)
    service.putrequest("DELETE", url)
    service.putheader("Host", NEXUSHOST)
    #service.putheader("User-Agent", "Nexus cleaner")
    service.putheader("User-Agent", "DevOps (RisksDevOps@mail.ru) cleaner")
    service.putheader("Content-type", "text/html; charset=\"UTF-8\"")
    service.putheader("Authorization", "Basic %s" % auth)
    service.endheaders()
    service.send("")
    statuscode, statusmessage, header = service.getreply()
    print("Response: {} {}.".format(statuscode, statusmessage))
    if statuscode == 401: #Unauthorized
      GLOBAL_DELETE_ERRORS = False
      return 2
    if statuscode == 204:
      GLOBAL_TOTAL_REMOVED += 1
      mon.push("nexus_cleaner_versions_list", GLOBAL_TOTAL_REMOVED, {"job": "nexus_cleaner", "list": "removed", "repo": format_args["repo"]})
  return 0

def parse_xml_content(artifact_name, content_items):
  items = []
  for content_item in content_items:
    leaf = content_item.find("./leaf").text
    relativePath = content_item.find("./relativePath").text
    lastmodified = content_item.find("./lastModified").text
    lastmodified_short = lastmodified[0:19]
    lastmodified_dt = datetime.datetime.strptime(lastmodified_short,"%Y-%m-%d %H:%M:%S")
    if (leaf == "false"):
      version = content_item.find("./text").text
      if is_version(version):
        item = {
          "name": artifact_name,
          "version": version,
          "cmp_ver": get_version(version),
          "modified": lastmodified_dt,
          "path": relativePath
        }
        items.append(item)
  return items

def get_version_listing_xml(nexus_repository, groupname, artifactname):
    global NEXUSUSERNAME, NEXUSPASSWORD
    conn = httplib.HTTPSConnection(NEXUSHOST, NEXUSPORT)
    url = NEXUSBASEURL + nexus_repository + "/content/" + groupname.replace(".","/") + "/" + artifactname + "/"
    conn.putrequest("GET", url)
    auth = string.strip(base64.encodestring(NEXUSUSERNAME + ':' + NEXUSPASSWORD))
    conn.putheader("Authorization", "Basic %s" % auth)
    conn.endheaders()
    conn.send("")
    response = conn.getresponse()
    if (response.status == 200):
        elem = ET.fromstring(response.read())
        return elem
    else:
        print("DEBUG: HTTP response isn't OK!")
        return None

def is_version(artifact):
  if re.match("^.*[0-9]+\.[0-9]+\.[0-9]+$", artifact):
    return True
  if re.match("^.+-SNAPSHOT$", artifact):
    return True
  return False

def get_version(name):
  m = re.search("^.*([0-9]+\.[0-9]+\.[0-9]+)$", name)
  if m:
    version = m.group(1)
    return version
  m = re.search("^.+-SNAPSHOT$", name)
  if m:
    version = "0.0.0"
    return version
  return 0

def compare_versions(a, b):
  am = re.search("^([0-9]+)\.([0-9]+)\.([0-9]+)$", a['cmp_ver'])
  bm = re.search("^([0-9]+)\.([0-9]+)\.([0-9]+)$", b['cmp_ver'])
  if am and bm:
    a1 = int(am.group(1))
    a2 = int(am.group(2))
    a3 = int(am.group(3))
    b1 = int(bm.group(1))
    b2 = int(bm.group(2))
    b3 = int(bm.group(3))
    if a1 > b1:
      return 1
    elif a1 < b1:
      return -1
    else:
      if a2 > b2:
        return 1
      elif a2 < b2:
        return -1
      else:
        if a3 > b3:
          return 1
        elif a3 < b3:
          return -1
        else:
          return 0

def is_doomed(item, keep_versions, lastmodified):
  if keep_versions:
    for keep_version in keep_versions:
      if re.match("^" + keep_version, item['cmp_ver']):
        return False
  elif lastmodified:
    if item['modified'] < lastmodified:
      return True
    else:
      return False
  else:
    print("Nothing to keep! Exiting...")
    sys.exit()
  return True

def get_keep_versions(items, keep_recent_minors, keep_recent_versions):
  keep = []
  count = 0
  items.sort(cmp=compare_versions, reverse=True)
  for item in items:
    if (keep_recent_minors and count < keep_recent_minors) or (keep_recent_versions and count < keep_recent_versions):
      m = re.search("^([0-9]+)\.([0-9]+)\.([0-9]+)$", item['cmp_ver'])
      if keep_recent_minors:
        version = "{}.{}".format(m.group(1), m.group(2))
      elif keep_recent_versions:
        version = "{}.{}.{}".format(m.group(1), m.group(2), m.group(3))
      if version not in keep:
        keep.append(version)
        count += 1
        if count == keep_recent_minors or count == keep_recent_versions:
          break
  return keep

def recreate_whole_list():
    fout = open('nexus_artifacts_list.txt', 'w')
    fout.close()

def recreate_keep_list():
    fout = open('nexus_artifacts_keep_list.txt', 'w')
    fout.close()
    fout = open('nexus_errors_list.txt', 'w')
    fout.close()

def recreate_doomed_list():
    fout = open('nexus_artifacts_doomed_list.txt', 'w')
    fout.close()

def save_artifact_to_doomed_list(name, version='', group=''):
  fout = open('nexus_artifacts_doomed_list.txt', 'a')
  fout.write("{}/{}/{}\n".format(group, name, version))
  fout.close()

def save_artifact_to_keep_list(name, version='', group=''):
  fout = open('nexus_artifacts_keep_list.txt', 'a')
  fout.write("{}/{}/{}\n".format(group, name, version))
  fout.close()

if __name__ == "__main__":
  main(sys.argv[1:])

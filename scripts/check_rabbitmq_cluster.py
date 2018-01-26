#!/usr/bin/python
"""
Nagios check to check RabbitMQ cluster for partitioning (split-brain)
"""

import argparse
import sys
import os.path
import time
import re


OK = 0
WARNING = 1
CRITICAL = 2
UNKNOWN = 3

parser = argparse.ArgumentParser(description='RabbitMQ cluster state check for Nagios.')
parser.add_argument('statsfile', nargs=1, type=str, help='file containing cluster status')
parser.add_argument('--freshness', nargs=1, type=int, required=False, default=300, help='update frequency of stats file in seconds, defaults to 300s')
args = parser.parse_args()

try:
    if time.time() - os.path.getmtime(args.statsfile[0]) > args.freshness:
        print 'CRITICAL - Cluster state file is too old'
        sys.exit(CRITICAL)
    data = ""
    with open(args.statsfile[0]) as fh:
        for line in fh:
            data += line.rstrip('\n')
    if not data:
        print 'UNKNOWN - Missing data for RabbitMQ cluster state'
        sys.exit(UNKNOWN)
    if not re.search('\{partitions,\[\]\}', data):
        print 'CRITICAL - Multiple partitions detected'
        sys.exit(CRITICAL)
    else:
        print 'OK - No partitioning detected'
        sys.exit(OK)
except Exception as e:
    print "UNKNOWN - Error during check processing: %s" % str(e)
    sys.exit(UNKNOWN)
